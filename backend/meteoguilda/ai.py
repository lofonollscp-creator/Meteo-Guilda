"""Síntesis de pronóstico con IA (OpenRouter, modelos gratuitos) + validación anti-alucinación."""
from __future__ import annotations

import json
import logging
import os
import re
import time

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger("ai")
BASE = "https://openrouter.ai/api/v1"
# Respaldo por si no se puede listar el catálogo. Los :free cambian a menudo.
DEFAULT_MODELS = ["openrouter/free"]

SYSTEM = """Eres el meteorólogo de Meteo Guilda. Recibes datos CRUDOS de varios modelos numéricos \
(media y rango entre modelos por tramos de 3 h) y observaciones reales de estaciones cercanas. \
Redacta en español un pronóstico claro y honesto.
Reglas estrictas:
- Usa SOLO cifras presentes en los datos; no inventes ni calcules otras. Viento en km/h (campo *_kmh).
- Si la confianza es baja o los modelos discrepan, dilo explícitamente.
- Si las observaciones reales contradicen al modelo, indícalo.
- No contradigas ni sustituyas avisos oficiales; recuerda consultarlos en actividades de riesgo.
- Responde SOLO con un JSON: {"resumen": str, "riesgos": [str], "ventanas": [str], \
"recomendaciones": [str], "confianza": "alta"|"media"|"baja"}"""


# ---------- proveedores ----------
PROVIDERS = {
    "server": {"label": "Servidor de Guilda (modelos gratuitos de OpenRouter)", "key": False},
    "openrouter": {"label": "OpenRouter (mi clave)", "key": True, "base": BASE},
    "openai": {"label": "OpenAI (mi clave)", "key": True, "base": "https://api.openai.com/v1"},
    "anthropic": {"label": "Anthropic · Claude (mi clave)", "key": True, "base": "https://api.anthropic.com/v1"},
    "mistral": {"label": "Mistral · UE (mi clave)", "key": True, "base": "https://api.mistral.ai/v1"},
}
# Sugerencias si no se puede listar el catálogo del proveedor
SUGGESTED = {"openrouter": ["openrouter/free", "anthropic/claude-sonnet-5-5", "openai/gpt-4o-mini", "google/gemini-2.5-flash"],
             "openai": ["gpt-4o-mini", "gpt-4o"], "anthropic": ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"],
             "mistral": ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"], "server": []}


@dataclass
class AIConfig:
    provider: str = "server"
    model: str = ""
    api_key: str | None = None


_MISTRAL_MODELO = re.compile(r"^[a-z0-9][a-z0-9.-]{1,79}$")      # «mistral-small-latest»; los de OpenRouter llevan «/» o «:free» y no valen aquí
_MISTRAL_NO_CHAT = re.compile(r"(embed|moderation|ocr|transcribe|tts|voxtral)", re.I)


def server_mistral_model() -> str:
    return os.environ.get("MG_MISTRAL_MODEL") or "mistral-small-latest"


def server_mistral(model: str | None = None) -> AIConfig | None:
    """Si el servidor tiene `MISTRAL_API_KEY`, su IA es Mistral (de pago, UE) en vez de los modelos gratuitos de OpenRouter. `model` es el que ha elegido
    la persona en sus ajustes; si está vacío o no es un nombre de modelo de Mistral (p. ej. uno de OpenRouter guardado antes), se usa `MG_MISTRAL_MODEL`."""
    key = (os.environ.get("MISTRAL_API_KEY") or "").strip()
    if not key:
        return None
    model = (model or "").strip()
    return AIConfig(provider="mistral", model=model if _MISTRAL_MODELO.match(model) else server_mistral_model(), api_key=key)


def server_ai() -> bool:
    """¿Tiene el servidor IA propia (Mistral u OpenRouter)?"""
    return bool(os.environ.get("MISTRAL_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def server_label() -> str:
    return "Servidor de Guilda (Mistral, UE)" if os.environ.get("MISTRAL_API_KEY") else PROVIDERS["server"]["label"]


def friendly_error(e: Exception) -> str:
    """Mensaje legible (sin URLs ni cuerpos de respuesta) para los errores de un proveedor de IA."""
    if isinstance(e, httpx.HTTPStatusError):
        s = e.response.status_code
        return {401: "La clave de API no es válida o ha caducado", 402: "Sin saldo en la cuenta del proveedor", 403: "El proveedor ha rechazado la petición (permisos de la clave)",
                404: "Ese modelo no existe en el proveedor", 429: "El proveedor limita el uso ahora mismo; prueba en unos minutos"}.get(s, f"El proveedor respondió con el error {s}")
    if isinstance(e, httpx.TimeoutException):
        return "El proveedor tardó demasiado en responder"
    if isinstance(e, httpx.HTTPError):
        return "No se pudo contactar con el proveedor"
    return str(e)[:160]


async def complete(http: httpx.AsyncClient, cfg: AIConfig, system: str, messages: list[dict], *, model: str | None = None,
                   max_tokens: int = 900, temperature: float = 0.2, timeout: float = 90) -> str:
    """Una respuesta de texto del proveedor elegido. Lanza httpx.HTTPError / ValueError si falla."""
    model = model or cfg.model
    if not model:
        raise ValueError("Falta el modelo")
    if cfg.provider == "anthropic":
        r = await http.post(f"{PROVIDERS['anthropic']['base']}/messages", timeout=timeout,
                            headers={"x-api-key": cfg.api_key or "", "anthropic-version": "2023-06-01"},
                            json={"model": model, "max_tokens": max_tokens, "system": system, "messages": messages})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    base = PROVIDERS.get(cfg.provider, PROVIDERS["server"]).get("base", BASE)
    key = cfg.api_key or (os.environ.get("OPENROUTER_API_KEY") if cfg.provider in ("server", "openrouter") else None)
    if not key:
        raise ValueError("Sin clave de API")
    r = await http.post(f"{base}/chat/completions", timeout=timeout, headers={"Authorization": f"Bearer {key}", "X-Title": "Meteo Guilda"},
                        json={"model": model, "temperature": temperature, "max_tokens": max_tokens,
                              "messages": [{"role": "system", "content": system}, *messages]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


async def list_models(http: httpx.AsyncClient, cfg: AIConfig) -> list[str]:
    """Modelos que ofrece el proveedor (con la clave de la persona); si falla, las sugerencias."""
    try:
        if cfg.provider == "server":
            return await free_models(http)
        if cfg.provider == "anthropic":
            r = await http.get(f"{PROVIDERS['anthropic']['base']}/models", params={"limit": 100}, timeout=15,
                               headers={"x-api-key": cfg.api_key or "", "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            return [m["id"] for m in r.json()["data"]]
        base = PROVIDERS[cfg.provider]["base"]
        r = await http.get(f"{base}/models", timeout=15, headers={"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {})
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json()["data"])
        if cfg.provider == "openai":
            ids = [i for i in ids if i.startswith(("gpt", "o1", "o3", "o4", "chatgpt"))]
        if cfg.provider == "mistral":
            ids = [i for i in ids if not _MISTRAL_NO_CHAT.search(i)]
        return ids[:400]
    except Exception as e:  # noqa: BLE001
        log.warning("modelos de %s: %s", cfg.provider, e)
        return SUGGESTED.get(cfg.provider, [])


class AIForecast(BaseModel):
    resumen: str = Field(min_length=10)
    riesgos: list[str] = []
    ventanas: list[str] = []
    recomendaciones: list[str] = []
    confianza: str = Field(pattern="^(alta|media|baja)$")


COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]


def compass(deg: float) -> str:
    return COMPASS[round(deg / 22.5) % 16]


def build_context(summary: dict, stations: list[dict]) -> dict:
    """Datos compactos y en unidades legibles (km/h, °C, mm) para la IA y para la web."""
    steps = []
    for s in summary["steps"]:
        row = {"time": s["time"], "confianza": s["confidence"]}
        for var, key in [("temperature_2m", "temp_c"), ("precipitation", "lluvia_mm"), ("cloud_cover", "nubes_pct"),
                         ("freezing_level_height", "isocero_m"), ("relative_humidity_2m", "humedad_pct"),
                         ("surface_pressure", "presion_hpa"), ("snowfall", "nieve_cm"), ("cape", "cape_jkg")]:
            if var in s:
                row[key] = s[var]
        for var, key in [("wind_speed_10m", "viento_kmh"), ("wind_gusts_10m", "racha_kmh")]:
            if var in s:
                row[key] = {k: round(v * 3.6) for k, v in s[var].items()}
        if "wind_direction_10m" in s:
            row["viento_dir"] = s["wind_direction_10m"]["mean"]
            row["viento_dir_txt"] = compass(row["viento_dir"])
        steps.append(row)
    days: dict[str, dict] = {}
    for r in steps:
        d = days.setdefault(r["time"][:10], {"fecha": r["time"][:10], "t": [], "lluvia_mm": 0.0, "racha": 0})
        if "temp_c" in r:
            d["t"] += [r["temp_c"]["min"], r["temp_c"]["max"]]
        d["lluvia_mm"] += r.get("lluvia_mm", {}).get("mean", 0)
        d["racha"] = max(d["racha"], r.get("racha_kmh", {}).get("max", 0))
    dias = [{"fecha": d["fecha"], "temp_min_c": round(min(d["t"])) if d["t"] else None,
             "temp_max_c": round(max(d["t"])) if d["t"] else None, "lluvia_mm": round(d["lluvia_mm"], 1),
             "racha_max_kmh": d["racha"]} for d in days.values()]
    kmh = lambda s: [None if v is None else round(v * 3.6) for v in s]  # noqa: E731
    por_modelo = {}
    for var, key in (("temperature_2m", "temp_c"), ("precipitation", "lluvia_mm"), ("wind_speed_10m", "viento_kmh"),
                     ("wind_gusts_10m", "racha_kmh"), ("cloud_cover", "nubes_pct")):
        if var in summary.get("por_modelo", {}):
            por_modelo[key] = {m: (kmh(s) if key in ("viento_kmh", "racha_kmh") else s) for m, s in summary["por_modelo"][var].items()}
    return {"ubicacion": {"lat": summary["lat"], "lon": summary["lon"], "altitud_m": summary["elevation_m"]},
            "modelos": summary["models"], "tramos": steps, "dias": dias, "estaciones_cercanas": stations, "por_modelo": por_modelo}


def ai_view(context: dict, n: int = 16) -> dict:
    """Subconjunto del contexto que se envía al LLM (48 h por tramos + resumen diario)."""
    return {**{k: v for k, v in context.items() if k != "por_modelo"},
            "tramos": [{k: v for k, v in t.items() if k not in ("viento_dir",)} for t in context["tramos"][:n]]}


def _collect(obj, key: str = "") -> list[tuple[str, float]]:
    """Pares (campo, valor) de todos los números; {mean,min,max} heredan el campo padre."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [(key, float(obj))]
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            out += _collect(v, key if k in ("mean", "min", "max") else k)
        return out
    if isinstance(obj, list):
        return [p for v in obj for p in _collect(v, key)]
    return []


# unidad en el texto -> fragmentos de nombre de campo donde puede aparecer ese valor
_UNIT_KEYS = {"c": ("temp",), "kmh": ("viento", "racha"), "mm": ("lluvia",),
              "pct": ("nubes", "humedad"), "hpa": ("presion",), "m": ("isocero", "altitud", "ascenso", "descenso", "ele")}
# El signo solo cuenta si no va pegado a otro número: en «8-11 °C» el guion es un rango, no un -11. Separador de miles: «2.262 m».
_UNIT_NUM = re.compile(r"(?<![\d.,])(-?)(\d+(?:[.,]\d+)*)\s*(°\s*C|ºC|º|km/h|m/s|mm|%|hPa|m)(?![\w/])")
_KIND = {"°C": "c", "ºC": "c", "º": "c", "km/h": "kmh", "m/s": "ms", "mm": "mm",
         "%": "pct", "hPa": "hpa", "m": "m"}


def unsupported_numbers(text: str, context: dict) -> list[str]:
    """Cifras con unidad que no aparecen (con tolerancia) en campos de esa misma unidad."""
    pairs = _collect(context)
    bad = []
    for m in _UNIT_NUM.finditer(text):
        kind = _KIND[m.group(3).replace(" ", "")]
        num = m.group(2)
        try:
            v = float(num.replace(".", "")) if kind == "m" and re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{3})+", num) else float(num.replace(",", "."))
        except ValueError:
            continue
        if m.group(1):
            v = -v
        if kind == "ms":
            v, kind = v * 3.6, "kmh"
        allowed = [a for k, a in pairs if any(x in k for x in _UNIT_KEYS[kind])]
        if not any(abs(v - a) <= max(1.5, 0.1 * abs(a)) for a in allowed):
            bad.append(m.group(0))
    return bad


def check(fc: AIForecast, context: dict) -> list[str]:
    text = " ".join([fc.resumen, *fc.riesgos, *fc.ventanas, *fc.recomendaciones])
    return unsupported_numbers(text, context)


_models_cache: tuple[float, list[str]] = (0, [])


async def free_models(client: httpx.AsyncClient) -> list[str]:
    """Modelos gratuitos disponibles ahora (cache 1 h). Prioriza MG_MODELS si existe."""
    global _models_cache
    env = os.environ.get("MG_MODELS")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    if time.time() - _models_cache[0] < 3600 and _models_cache[1]:
        return _models_cache[1]
    try:
        r = await client.get(f"{BASE}/models", timeout=20)
        r.raise_for_status()
        free = [m for m in r.json()["data"]
                if m["id"].endswith(":free") or all(float(v) == 0 for v in
                                                    (m.get("pricing", {}).get("prompt", 1),
                                                     m.get("pricing", {}).get("completion", 1)))]
        free.sort(key=lambda m: m.get("context_length", 0), reverse=True)
        ids = [m["id"] for m in free][:12]
        if ids:
            _models_cache = (time.time(), ids)
            return ids
    except Exception as e:  # noqa: BLE001
        log.warning("no se pudo listar modelos: %s", e)
    return _models_cache[1] or DEFAULT_MODELS


# ---------- salud de los modelos gratuitos ----------
# Los :free de OpenRouter se saturan (429), se retiran o devuelven basura. Se recuerda cómo les fue para no volver a llamar a uno caído en cada petición.
_PAUSA = {"429": 300, "cifras": 900, "vacio": 900, "error": 1800}      # segundos de pausa según el tipo de fallo
_health: dict[str, dict] = {}


def note(model: str, kind: str | None) -> None:
    """kind=None: respondió bien. Si no: '429' | 'cifras' | 'vacio' | 'error'."""
    h = _health.setdefault(model, {})
    if kind is None:
        h["ok"], h["until"], h["kind"] = time.time(), 0, None
    else:
        h["fail"], h["until"], h["kind"] = time.time(), time.time() + _PAUSA.get(kind, 600), kind


def estado_modelos(now: float | None = None) -> list[dict]:
    """Estado de los modelos gratuitos que se han probado desde el arranque: disponible, en pausa (con segundos que faltan y motivo) o sin respuesta reciente."""
    now = time.time() if now is None else now
    out = []
    for m, h in _health.items():
        pausa = h.get("until", 0) - now
        estado = "pausado" if pausa > 0 else "disponible" if h.get("ok") else "sin probar"
        out.append({"modelo": m, "estado": estado, "segundos": max(0, round(pausa)), "motivo": h.get("kind") if pausa > 0 else None,
                    "ultimo_ok": h.get("ok") or None})
    out.sort(key=lambda x: (x["estado"] != "disponible", -(x["ultimo_ok"] or 0)))
    return out


def kind_of(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (429, 503, 502):
        return "429"
    if isinstance(e, ValidationError | ValueError | TypeError | KeyError | IndexError | AttributeError):
        return "vacio"
    return "error"


async def ranked_free_models(client: httpx.AsyncClient, n: int = 5) -> list[str]:
    """Modelos gratuitos por orden de probabilidad de acertar: primero los que respondieron hace poco, luego los no probados; los que están
    en pausa quedan fuera (salvo que no quede ninguno). `openrouter/free` (el enrutador de OpenRouter, que elige uno libre) va siempre de reserva."""
    now = time.time()
    pool = list(dict.fromkeys([*(await free_models(client)), "openrouter/free"]))
    live = [m for m in pool if _health.get(m, {}).get("until", 0) <= now]
    live.sort(key=lambda m: -_health.get(m, {}).get("ok", 0))
    if "openrouter/free" in live and live.index("openrouter/free") >= n:
        live[n - 1] = "openrouter/free"
    return (live or pool)[:n]


def why_failed(kinds: list[str]) -> str:
    if not kinds:
        return "La IA no ha podido responder ahora."
    if "429" in kinds:
        return "Los modelos gratuitos están saturados ahora mismo (límite de uso de OpenRouter)."
    if "cifras" in kinds:
        return "La IA respondió con cifras que no estaban en los datos y se descartó por seguridad."
    return "Los modelos gratuitos no han devuelto una respuesta válida."


def _extract_json(txt: str | None) -> dict:
    if not txt:
        raise ValueError("Respuesta vacía del modelo")
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


async def generate(context: dict, client: httpx.AsyncClient, api_key: str | None = None,
                   system: str = SYSTEM, trim: bool = True, cfg: AIConfig | None = None) -> tuple[AIForecast, str] | None:
    """Con `cfg` de otro proveedor, un intento con el modelo elegido; si no, prueba modelos gratuitos en orden.
    Devuelve (pronóstico, modelo) o None."""
    if trim:
        context = ai_view(context)
    user = "Datos:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    if cfg and cfg.provider != "server":
        try:
            fc = AIForecast(**_extract_json(await complete(client, cfg, system, [{"role": "user", "content": user}], max_tokens=1200)))
            bad = check(fc, context)
            if bad:
                log.warning("%s: cifras sin respaldo %s; descartado", cfg.model, bad)
                return None
            return fc, cfg.model
        except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, ValueError, ValidationError) as e:
            log.warning("%s/%s falló: %s", cfg.provider, cfg.model, e)
            return None
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    models = [cfg.model] if cfg and cfg.model else await ranked_free_models(client)
    kinds: list[str] = []
    for model in models:
        try:
            r = await client.post(
                f"{BASE}/chat/completions", timeout=60,
                headers={"Authorization": f"Bearer {api_key}", "X-Title": "Meteo Guilda"},
                json={"model": model, "temperature": 0.2, "max_tokens": 1200,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]})
            r.raise_for_status()
            fc = AIForecast(**_extract_json(r.json()["choices"][0]["message"].get("content")))
            bad = check(fc, context)
            if bad:
                log.warning("%s: cifras sin respaldo %s; descartado", model, bad)
                note(model, "cifras"); kinds.append("cifras")
                continue
            note(model, None)
            return fc, model
        except (httpx.HTTPError, KeyError, IndexError, TypeError, AttributeError, ValueError, ValidationError) as e:
            log.warning("%s falló: %s", model, e)
            k = kind_of(e); note(model, k); kinds.append(k)
    generate.last_why = why_failed(kinds)
    return None


generate.last_why = ""


def rules_forecast(context: dict) -> AIForecast:
    """Resumen determinista cuando la IA no está disponible."""
    t = context["tramos"][:16]
    temps = [s["temp_c"]["mean"] for s in t if "temp_c" in s]
    rain = sum(s["lluvia_mm"]["mean"] for s in t if "lluvia_mm" in s)
    gust = max((s["racha_kmh"]["max"] for s in t if "racha_kmh" in s), default=0)
    conf = "baja" if any(s["confianza"] == "baja" for s in t) else \
        "media" if any(s["confianza"] == "media" for s in t) else "alta"
    riesgos = []
    if gust >= 60:
        riesgos.append(f"Rachas de hasta {gust} km/h.")
    if rain >= 10:
        riesgos.append(f"Precipitación acumulada de unos {round(rain)} mm.")
    resumen = (f"Temperaturas entre {round(min(temps))} °C y {round(max(temps))} °C en las próximas "
               f"{len(t) * 3} h, rachas máximas de {gust} km/h y {round(rain)} mm de lluvia acumulada."
               ) if temps else "Sin datos suficientes."
    return AIForecast(resumen=resumen, riesgos=riesgos, ventanas=[], recomendaciones=[
        "Consulta los avisos oficiales antes de salir."], confianza=conf)


ROUTE_SYSTEM = """Eres el meteorólogo de Meteo Guilda y evalúas una RUTA (a pie o en coche). Recibes la ruta \
muestreada con la meteo prevista a la hora de paso en cada punto, el nivel de riesgo calculado por reglas \
(0 verde, 1 ámbar, 2 rojo) y las mejores ventanas de salida. Redacta en español un briefing breve y útil.
Reglas estrictas:
- Usa SOLO cifras presentes en los datos; no inventes ni recalcules. Viento en km/h.
- No cambies los niveles de riesgo calculados; explica los motivos y propone plan B (retrasar, acortar, alternativa).
- Si es montaña, recuerda hora límite de descenso y consultar avisos oficiales.
- Responde SOLO con un JSON: {"resumen": str, "riesgos": [str], "ventanas": [str], \
"recomendaciones": [str], "confianza": "alta"|"media"|"baja"}"""
