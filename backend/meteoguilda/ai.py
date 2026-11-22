"""Síntesis de pronóstico con IA (OpenRouter, modelos gratuitos) + validación anti-alucinación."""
from __future__ import annotations

import json
import logging
import os
import re
import time

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
    return {"ubicacion": {"lat": summary["lat"], "lon": summary["lon"], "altitud_m": summary["elevation_m"]},
            "modelos": summary["models"], "tramos": steps, "dias": dias, "estaciones_cercanas": stations}


def ai_view(context: dict, n: int = 16) -> dict:
    """Subconjunto del contexto que se envía al LLM (48 h por tramos + resumen diario)."""
    return {**context, "tramos": [{k: v for k, v in t.items() if k not in ("viento_dir",)}
                                  for t in context["tramos"][:n]]}


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
_UNIT_NUM = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*(°\s*C|ºC|º|km/h|m/s|mm|%|hPa|m)(?![\w/])")
_KIND = {"°C": "c", "ºC": "c", "º": "c", "km/h": "kmh", "m/s": "ms", "mm": "mm",
         "%": "pct", "hPa": "hpa", "m": "m"}


def unsupported_numbers(text: str, context: dict) -> list[str]:
    """Cifras con unidad que no aparecen (con tolerancia) en campos de esa misma unidad."""
    pairs = _collect(context)
    bad = []
    for m in _UNIT_NUM.finditer(text):
        v = float(m.group(1).replace(",", "."))
        kind = _KIND[m.group(2).replace(" ", "")]
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
        ids = [m["id"] for m in free][:6]
        if ids:
            _models_cache = (time.time(), ids)
            return ids
    except Exception as e:  # noqa: BLE001
        log.warning("no se pudo listar modelos: %s", e)
    return _models_cache[1] or DEFAULT_MODELS


def _extract_json(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


async def generate(context: dict, client: httpx.AsyncClient, api_key: str | None = None,
                   system: str = SYSTEM, trim: bool = True) -> tuple[AIForecast, str] | None:
    """Prueba modelos gratuitos en orden; devuelve (pronóstico, modelo) o None."""
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    if trim:
        context = ai_view(context)
    user = "Datos:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    for model in await free_models(client):
        try:
            r = await client.post(
                f"{BASE}/chat/completions", timeout=90,
                headers={"Authorization": f"Bearer {api_key}", "X-Title": "Meteo Guilda"},
                json={"model": model, "temperature": 0.2,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]})
            r.raise_for_status()
            fc = AIForecast(**_extract_json(r.json()["choices"][0]["message"]["content"]))
            bad = check(fc, context)
            if bad:
                log.warning("%s: cifras sin respaldo %s; descartado", model, bad)
                continue
            return fc, model
        except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as e:
            log.warning("%s falló: %s", model, e)
    return None


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
