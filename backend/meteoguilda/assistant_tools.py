"""Herramientas del asistente de IA: lo mismo que se hace a mano en la app (buscar un pueblo, previsión, mar, aire, rutas, repetidores,
satélites…) pero pedido por chat. Cada herramienta llama a la API propia (misma validación, cachés y límites que la web y el MCP).

Funciona con cualquier modelo, sin depender de «function calling»: el modelo escribe una línea
    <tool>{"name":"prevision","args":{"lugar":"Sitges"}}</tool>
el servidor la ejecuta y le devuelve el resultado, hasta MAX_PASOS vueltas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import ai, geocode

log = logging.getLogger("meteoguilda.asistente")

MAX_PASOS = 4          # vueltas modelo -> herramientas -> modelo
MAX_LLAMADAS = 3       # herramientas por vuelta
MAX_CHARS = 3200       # resultado devuelto al modelo por herramienta

# nombre -> (descripción para el modelo, argumentos). «lugar» = nombre de pueblo/ciudad, «aquí» o «mi ubicación».
CATALOGO: dict[str, tuple[str, str]] = {
    "buscar_lugar": ("Busca pueblos, ciudades o parajes por nombre y devuelve coordenadas.", "consulta"),
    "prevision": ("Previsión de modelos (hasta 5 días, medias y confianza) de un lugar.", "lugar"),
    "avisos": ("Avisos oficiales vigentes (AEMET) en un lugar.", "lugar"),
    "aire": ("Calidad del aire, UV y polen.", "lugar"),
    "mar": ("Estado del mar: oleaje, periodo, agua (costa).", "lugar"),
    "comparar": ("Compara 2-4 lugares (cifras de 48 h y veredicto).", "lugares (lista de nombres)"),
    "actividad": ("Mejores ventanas para senderismo, ciclismo, dron, colada, estrellas o playa.", "actividad, lugar"),
    "ruta": ("Ruta a pie o en coche entre lugares con la meteo a la hora de paso y nivel de riesgo.", "desde, hasta, modo (foot|car), vias (lista opcional)"),
    "observaciones": ("Observaciones reales cercanas (APRS, METAR, XEMA).", "lugar, km"),
    "propagacion": ("Propagación VHF/UHF (conductos, esporádica E) en un lugar.", "lugar"),
    "condiciones_hf": ("Índices solares y condiciones HF actuales.", ""),
    "repetidores": ("Repetidores de radioafición cercanos a un lugar.", "lugar, km, banda (2m|70cm…), modo (FM|DMR…)"),
    "satelites": ("Próximos pasos de satélites de radioafición.", "lugar, horas"),
    "astro": ("Sol y luna: orto, ocaso, fase.", "lugar"),
    "portatil": ("Mejor momento para operar en portátil al aire libre.", "lugar"),
    "ventanas_radio": ("Ventanas para operar en radio en un lugar.", "lugar, modo (fijo|portatil)"),
    "guardar_lugar": ("Propone guardar un lugar en los favoritos de la persona (ella confirma con un botón).", "lugar"),
    "usar_como_mi_ubicacion": ("Propone fijar un lugar como «Mi ubicación» (ella confirma con un botón).", "lugar"),
    "anotar_qso": ("Propone anotar un contacto (QSO) en el cuaderno QSL (ella confirma con un botón; no se guarda nada antes).", "indicativo, banda, modo, frecuencia_mhz, rst_enviado, rst_recibido, nombre, locator, notas"),
    "avisar_paso": ("Propone un aviso 5 min antes del próximo paso de un satélite sobre un lugar (ella confirma; suena aunque cierre la pestaña si tiene las notificaciones activadas).", "satelite (ISS, SO-50…), lugar"),
    "recordar": ("Propone recordar una preferencia de la persona para futuras respuestas (ella confirma con un botón). SOLO si ella lo pide («recuerda que…») y nunca datos personales, de salud o creencias.", "texto (≤140 caracteres), categoria (actividad, lugar, radio, unidades, estilo, otro)"),
    "locator": ("Locator Maidenhead de un lugar, o coordenadas de un locator.", "lugar o locator"),
}


def catalogo_texto() -> str:
    filas = [f"- {n}({a}): {d}" if a else f"- {n}: {d}" for n, (d, a) in CATALOGO.items()]
    return (
        "\n\nHERRAMIENTAS. Puedes consultar datos reales tú mismo para cualquier lugar, no solo el que se está viendo. Para llamar a una escribe, "
        "sola en su línea y sin nada más en esa línea, `<tool>{\"name\":\"NOMBRE\",\"args\":{...}}</tool>` (hasta 3 por mensaje). "
        "Recibirás el resultado y entonces respondes. Los argumentos `lugar`/`desde`/`hasta` admiten el nombre de un pueblo o ciudad tal como lo diría "
        "una persona («Sitges», «Vielha»), «aquí» o «mi ubicación»: la búsqueda del pueblo es automática, no hace falta buscar antes. "
        "Si hay varios con el mismo nombre añade la comarca o el país («Santa Cruz, Tenerife»).\n" + "\n".join(filas) +
        "\nUsa herramientas cuando pidan un lugar distinto del que ya tienes en los datos, o algo que esos datos no traen (mar, aire, repetidores, "
        "satélites, rutas…). No las uses para lo que ya está en «Datos del lugar». Tras recibir resultados, responde con ellos sin repetir la llamada. "
        "Cuando hayas usado un lugar, di cuál (con su comarca) para que se vea que es el correcto. Si una herramienta falla, dilo sin inventar."
    )


_RE = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.S)


def parse(text: str) -> list[dict]:
    out = []
    for m in _RE.finditer(text or ""):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name") in CATALOGO:
            a = d.get("args")
            out.append({"name": d["name"], "args": a if isinstance(a, dict) else {}})
    return out[:MAX_LLAMADAS]


def limpiar(text: str) -> str:
    return re.sub(r"</?tool>", "", _RE.sub("", text or "")).strip()


def encoger(obj: Any, prof: int = 0) -> Any:
    """Recorta listas y números para que el resultado quepa en el contexto de modelos pequeños."""
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, str):
        return obj[:200]
    if isinstance(obj, dict):
        return {k: encoger(v, prof + 1) for k, v in obj.items() if v is not None and k not in ("geometria", "series", "polyline")}
    if isinstance(obj, list):
        return [encoger(v, prof + 1) for v in obj[:8 if prof else 10]]
    return obj


def texto(obj: Any) -> str:
    s = json.dumps(encoger(obj), ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + "…[recortado]"


@dataclass
class Entorno:
    """Lo que la herramienta necesita saber de la petición: cliente HTTP de la app, IP real y los lugares conocidos."""
    app: Any
    http: httpx.AsyncClient
    ip: str = ""
    viendo: dict | None = None        # {lat, lon, name}
    casa: dict | None = None          # «Mi ubicación»
    memoria: bool = False             # la persona ha activado la memoria de preferencias (opt-in, ver memoria.py)
    usados: list[dict] = field(default_factory=list)   # lugares resueltos, para enlazarlos en la web


class ErrorHerramienta(Exception):
    pass


async def _api(env: Entorno, method: str, path: str, **kw) -> Any:
    h = {"x-forwarded-for": env.ip} if env.ip else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://asistente", timeout=60, headers=h) as c:
        r = await c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            det = r.json().get("detail", r.text)
        except ValueError:
            det = r.text
        raise ErrorHerramienta(f"{r.status_code}: {det}")
    return r.json()


async def resolver(env: Entorno, q: Any) -> dict:
    """Nombre -> {name, lat, lon}. Admite «aquí»/«mi ubicación», «lat,lon» o un nombre de lugar."""
    if isinstance(q, dict) and "lat" in q and "lon" in q:
        return {"name": str(q.get("name") or f"{float(q['lat']):.3f}, {float(q['lon']):.3f}"), "lat": float(q["lat"]), "lon": float(q["lon"])}
    s = str(q or "").strip()
    if not s:
        if env.viendo:
            return env.viendo
        raise ErrorHerramienta("Falta el lugar")
    if re.fullmatch(r"-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?", s):
        la, lo = (float(x) for x in s.split(","))
        return {"name": f"{la:.3f}, {lo:.3f}", "lat": la, "lon": lo}
    if s.lower() in ("aqui", "aquí", "mi ubicacion", "mi ubicación", "donde estoy", "casa"):
        r = env.casa or env.viendo
        if not r:
            raise ErrorHerramienta("No hay «Mi ubicación» guardada: pídele que la active en Ajustes o que diga el nombre del lugar")
        return r
    hits = await geocode.search(env.http, s, count=5)
    if not hits and "," in s:                       # «Santa Cruz, Tenerife» -> prueba con la primera parte y filtra por el resto
        hits = await geocode.search(env.http, s.split(",")[0].strip(), count=8)
    if not hits:
        raise ErrorHerramienta(f"No he encontrado ningún lugar llamado «{s}»")
    if "," in s:
        resto = s.split(",", 1)[1].strip().lower()
        fil = [h for h in hits if resto in " ".join(str(h.get(k) or "") for k in ("admin1", "admin2", "country", "cc")).lower()]
        hits = fil or hits
    h = hits[0]
    sub = ", ".join(x for x in (h.get("admin2") or h.get("admin1"), h.get("country")) if x)
    return {"name": h["name"] + (f" ({sub})" if sub else ""), "lat": h["lat"], "lon": h["lon"], "ele": h.get("ele")}


def _lug(env: Entorno, p: dict) -> dict:
    d = {"name": p["name"], "lat": round(p["lat"], 4), "lon": round(p["lon"], 4)}
    if d not in env.usados:
        env.usados.append(d)
    return d


_TIEMPO = re.compile(r"tiempo|llov|lluvi|chub|temperatur|viento|fr[ií]o|calor|nube|nublad|nieve|nevar|niebla|previsi|pron[oó]stico|ma[ñn]ana|hoy\b|esta (tarde|noche)|fin de semana|s[aá]bado|domingo|lunes|martes|mi[eé]rcoles|jueves|viernes|paraguas|abrigo|\bropa\b|salir|caminar|excursi[oó]n|playa|helada|tormenta", re.I)


def es_pregunta_de_tiempo(texto: str) -> bool:
    return bool(_TIEMPO.search(texto or ""))


def lugar_de_contexto(texto: str, env: "Entorno") -> dict | None:
    """El lugar al que se refiere la pregunta cuando no nombra otro: «aquí/mi ubicación» -> Mi ubicación; si no, el que se está viendo."""
    aqui = re.search(r"\baqu[ií]\b|mi ubicaci[oó]n|donde estoy|\bcasa\b", texto or "", re.I)
    return (env.casa or env.viendo) if aqui else (env.viendo or env.casa)


def _m(x, k="mean"):
    return x.get(k) if isinstance(x, dict) else x


def tarjeta_prevision(p: dict, datos: dict) -> dict | None:
    """Datos estructurados para pintar la tarjeta en el chat (la IA no los toca: salen tal cual de los modelos)."""
    pasos = datos.get("tramos") or []
    if not pasos:
        return None
    a = pasos[0]
    return {"tipo": "prevision", "lugar": p["name"], "lat": round(p["lat"], 4), "lon": round(p["lon"], 4),
            "ahora": {"temp": _m(a.get("temp_c")), "viento": _m(a.get("viento_kmh")), "dir": a.get("viento_dir_txt"), "lluvia": _m(a.get("lluvia_mm")),
                      "nubes": _m(a.get("nubes_pct")), "confianza": a.get("confianza")},
            "serie": [_m(t.get("temp_c")) for t in pasos[:12]],
            "lluvia": [_m(t.get("lluvia_mm")) for t in pasos[:12]], "viento_serie": [_m(t.get("viento_kmh")) for t in pasos[:12]],
            "horas": [t.get("time") for t in pasos[:12]], "modelos": len(datos.get("modelos") or []), "contraste": datos.get("contraste"),
            "dias": [{k: d.get(k) for k in ("fecha", "temp_min_c", "temp_max_c", "lluvia_mm", "racha_max_kmh")} for d in (datos.get("dias") or [])[:4]]}


def tarjeta_comparar(r: dict) -> dict | None:
    """Tarjeta de comparación: una fila por lugar con las cifras de 48 h tal cual salen de los modelos."""
    ls = [{k: f.get(k) for k in ("nombre", "lat", "lon", "ahora_c", "min_c", "max_c", "lluvia_48h_mm", "racha_max_kmh")} for f in (r.get("lugares") or [])]
    return {"tipo": "comparar", "lugares": ls, "veredicto": (r.get("veredicto") or [])[:3]} if len(ls) >= 2 else None


def tarjeta_mar(p: dict, r: dict) -> dict | None:
    if not r.get("hay_mar"):
        return None
    hs = r.get("horas") or []
    return {"tipo": "mar", "lugar": p["name"], "lat": round(p["lat"], 4), "lon": round(p["lon"], 4), "ahora": r.get("ahora"), "maximo": r.get("maximo_48h"),
            "serie": [h.get("ola_m") for h in hs[::3][:16]], "horas": [h.get("hora") for h in hs[::3][:16]]}


def tarjeta_satelites(p: dict, r: dict) -> dict | None:
    ps = [{k: x.get(k) for k in ("satelite", "aos", "aos_az", "max_el", "duracion_s")} for x in (r.get("pasos") or [])[:4]]
    return {"tipo": "satelites", "lugar": p["name"], "pasos": ps} if ps else None


async def ejecutar(env: Entorno, nombre: str, a: dict) -> dict:
    """Ejecuta una herramienta; devuelve {nombre, lugar?, ok, resultado|error}."""
    out: dict = {"nombre": nombre}
    try:
        if nombre == "buscar_lugar":
            q = str(a.get("consulta") or a.get("lugar") or "")
            if len(q) < 2:
                raise ErrorHerramienta("Escribe al menos 2 letras")
            res = await _api(env, "GET", "/api/search", params={"q": q})
            out["resultado"] = [{k: h.get(k) for k in ("name", "admin1", "admin2", "country", "lat", "lon", "ele", "pop")} for h in res[:5]]
            for h in res[:1]:
                _lug(env, h)
            return {**out, "ok": True}
        if nombre == "condiciones_hf":
            return {**out, "ok": True, "resultado": await _api(env, "GET", "/api/solar")}
        if nombre == "comparar":
            lst = a.get("lugares") or []
            if not isinstance(lst, list) or not 2 <= len(lst) <= 4:
                raise ErrorHerramienta("Indica entre 2 y 4 lugares")
            ps = await asyncio.gather(*(resolver(env, x) for x in lst))
            pts = ";".join(f"{p['lat']},{p['lon']},{p['name'].replace(',', ' ').replace(';', ' ')}" for p in ps)
            out["lugar"] = ", ".join(p["name"] for p in ps)
            for p in ps:
                _lug(env, p)
            r = await _api(env, "GET", "/api/compare", params={"pts": pts})
            out["tarjeta"] = tarjeta_comparar(r)
            return {**out, "ok": True, "resultado": r}
        if nombre == "ruta":
            pts = [a.get("desde"), *(a.get("vias") or []), a.get("hasta")]
            ps = await asyncio.gather(*(resolver(env, x) for x in pts))
            body = {"waypoints": [{"lat": p["lat"], "lon": p["lon"]} for p in ps], "mode": "car" if a.get("modo") == "car" else "foot"}
            out["lugar"] = " → ".join(p["name"] for p in ps)
            for p in ps:
                _lug(env, p)
            r = await _api(env, "POST", "/api/route", json=body)
            r = {k: r.get(k) for k in ("modo", "distancia_km", "duracion_min", "ascenso_m", "descenso_m", "salida", "nivel_global", "nivel_texto", "ventanas", "muestras")}
            r["muestras"] = [{k: m.get(k) for k in ("d_km", "ele", "t", "nivel", "motivos")} for m in (r.get("muestras") or [])][:8]
            out["tarjeta"] = {"tipo": "ruta", "lugar": out["lugar"], "km": r.get("distancia_km"), "min": r.get("duracion_min"), "asc": r.get("ascenso_m"),
                              "desc": r.get("descenso_m"), "nivel": r.get("nivel_global"), "texto": r.get("nivel_texto"), "modo": r.get("modo")}
            return {**out, "ok": True, "resultado": r}
        if nombre == "anotar_qso":
            cs = str(a.get("indicativo") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?", cs):
                raise ErrorHerramienta("Falta un indicativo válido (por ejemplo EA3XYZ)")
            q = {"call": cs, "band": a.get("banda"), "mode": (str(a["modo"]).upper() if a.get("modo") else None), "freq_mhz": a.get("frecuencia_mhz"),
                 "rst_sent": a.get("rst_enviado"), "rst_rcvd": a.get("rst_recibido"), "name": a.get("nombre"), "grid": a.get("locator"), "notes": (str(a["notas"])[:200] if a.get("notas") else None)}
            try:
                q["freq_mhz"] = float(q["freq_mhz"]) if q["freq_mhz"] not in (None, "") else None
            except (TypeError, ValueError) as e:
                raise ErrorHerramienta("La frecuencia debe ser un número en MHz") from e
            q = {k: (str(v)[:40] if isinstance(v, str) else v) for k, v in q.items() if v not in (None, "")}
            out["tarjeta"] = {"tipo": "accion", "accion": "qso", "lugar": cs, "qso": q}
            return {**out, "ok": True, "resultado": {"propuesta": q, "nota": "Se le ha mostrado un botón de confirmación; no se anota nada hasta que lo pulse. Díselo así."}}
        if nombre == "recordar":
            from fastapi import HTTPException
            from . import memoria
            if not env.memoria:
                raise ErrorHerramienta("La memoria de preferencias está desactivada: la persona puede activarla en Ajustes → Privacidad y memoria")
            try:
                texto, cat = memoria.validar(str(a.get("texto") or ""), str(a.get("categoria") or "otro"))
            except HTTPException as e:
                raise ErrorHerramienta(str(e.detail)) from e
            out["tarjeta"] = {"tipo": "accion", "accion": "memoria", "lugar": texto, "memoria": {"texto": texto, "cat": cat}}
            return {**out, "ok": True, "resultado": {"propuesta": texto, "nota": "Se le ha mostrado un botón «Recordar»; no se guarda nada hasta que lo pulse. Díselo así."}}
        # resto: un solo lugar
        q = a.get("lugar") or a.get("consulta") or a.get("locator")
        if nombre == "locator" and re.fullmatch(r"[A-Ra-r]{2}\d\d([A-Xa-x]{2})?", str(q or "").strip()):
            return {**out, "ok": True, "resultado": await _api(env, "GET", "/api/locator", params={"loc": str(q).strip()})}
        p = await resolver(env, q)
        out["lugar"] = p["name"]
        c = {"lat": p["lat"], "lon": p["lon"]}
        out["lugar_ref"] = _lug(env, p)
        if nombre == "prevision":
            d = await _api(env, "GET", "/api/forecast", params=c)
            r = {"lugar": p["name"], "datos": ai.ai_view(d["datos"], 12)}
            out["tarjeta"] = tarjeta_prevision(p, d["datos"])
        elif nombre == "avisos":
            r = await _api(env, "GET", "/api/avisos", params=c)
        elif nombre == "aire":
            r = await _api(env, "GET", "/api/air", params=c)
        elif nombre == "mar":
            r = await _api(env, "GET", "/api/mar", params=c)
            out["tarjeta"] = tarjeta_mar(p, r)
        elif nombre == "actividad":
            r = await _api(env, "GET", "/api/activity", params={**c, "activity": str(a.get("actividad") or "")})
        elif nombre == "observaciones":
            r = await _api(env, "GET", "/api/observations", params={**c, "km": min(max(float(a.get("km") or 40), 1), 150)})
        elif nombre == "propagacion":
            r = await _api(env, "GET", "/api/propagation", params=c)
        elif nombre == "repetidores":
            prm = {**c, "km": min(max(float(a.get("km") or 50), 1), 300), "limit": 10}
            if a.get("banda"):
                prm["band"] = str(a["banda"])
            if a.get("modo"):
                prm["mode"] = str(a["modo"])
            r = [{k: x.get(k) for k in ("name", "callsign", "band", "freq_mhz", "shift_khz", "tone_hz", "mode", "network", "dist_km")}
                 for x in await _api(env, "GET", "/api/repeaters/near", params=prm)]
        elif nombre == "satelites":
            r = await _api(env, "GET", "/api/satellites/passes", params={**c, "hours": min(max(float(a.get("horas") or 24), 1), 48)})
            out["tarjeta"] = tarjeta_satelites(p, r)
        elif nombre == "avisar_paso":
            r = await _api(env, "GET", "/api/satellites/passes", params={**c, "hours": 48})
            want = str(a.get("satelite") or "").strip().lower()
            limite = (datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat()
            pasos = [x for x in r.get("pasos") or [] if x["aos"] > limite and (not want or want in x["satelite"].lower())]
            if not pasos:
                raise ErrorHerramienta("No hay pasos próximos de ese satélite sobre ese lugar")
            x = pasos[0]
            out["tarjeta"] = {"tipo": "accion", "accion": "paso", "lugar": p["name"], "paso": {k: x.get(k) for k in ("satelite", "aos", "aos_az", "max_el", "duracion_s")}}
            r = {"propuesta": out["tarjeta"]["paso"], "nota": "Se le ha mostrado un botón de confirmación; el aviso solo suena con la pestaña abierta. Díselo así."}
        elif nombre == "astro":
            r = await _api(env, "GET", "/api/astro", params=c)
        elif nombre == "portatil":
            r = await _api(env, "GET", "/api/portable", params=c)
        elif nombre == "ventanas_radio":
            r = await _api(env, "GET", "/api/radio/ventanas", params={**c, "modo": "portatil" if a.get("modo") == "portatil" else "fijo"})
        elif nombre == "locator":
            r = await _api(env, "GET", "/api/locator", params=c)
        elif nombre in ("guardar_lugar", "usar_como_mi_ubicacion"):
            out["tarjeta"] = {"tipo": "accion", "accion": "favorito" if nombre == "guardar_lugar" else "ubicacion", "lugar": p["name"], "lat": round(p["lat"], 4), "lon": round(p["lon"], 4)}
            r = {"propuesta": p["name"], "nota": "Se le ha mostrado un botón de confirmación; no se guarda nada hasta que lo pulse. Díselo así."}
        else:
            raise ErrorHerramienta("Herramienta desconocida")
        return {**out, "ok": True, "resultado": r}
    except ErrorHerramienta as e:
        return {**out, "ok": False, "error": str(e)}
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
        log.warning("herramienta %s: %s", nombre, e)
        return {**out, "ok": False, "error": f"No se pudo obtener: {type(e).__name__}"}


async def ejecutar_todas(env: Entorno, llamadas: list[dict]) -> list[dict]:
    return await asyncio.gather(*(ejecutar(env, c["name"], c["args"]) for c in llamadas))


def respuesta_para_modelo(llamadas: list[dict], res: list[dict], ultima: bool) -> str:
    partes = []
    for c, r in zip(llamadas, res):
        cab = f"[{c['name']} {json.dumps(c['args'], ensure_ascii=False)}]"
        partes.append(cab + " " + (texto(r["resultado"]) if r["ok"] else f"ERROR: {r['error']}"))
    fin = ("Responde ya a la persona con estos datos; no puedes llamar más herramientas." if ultima
           else "Si ya tienes lo necesario responde a la persona sin más llamadas; si no, pide otra herramienta.")
    return "Resultados de las herramientas:\n" + "\n".join(partes) + "\n\n" + fin


ETIQUETAS = {"buscar_lugar": "Búsqueda de lugar", "prevision": "Previsión de modelos", "avisos": "Avisos oficiales", "aire": "Calidad del aire",
             "mar": "Estado del mar", "comparar": "Comparación", "actividad": "Ventanas de actividad", "ruta": "Ruta con meteo",
             "observaciones": "Estaciones cercanas", "propagacion": "Propagación VHF", "condiciones_hf": "Condiciones HF", "repetidores": "Repetidores",
             "satelites": "Pasos de satélite", "astro": "Sol y luna", "portatil": "Operación portátil", "ventanas_radio": "Ventanas de radio",
             "locator": "Locator", "anotar_qso": "Anotar contacto", "avisar_paso": "Aviso de paso", "recordar": "Recordar preferencia", "guardar_lugar": "Guardar lugar", "usar_como_mi_ubicacion": "Mi ubicación"}


def resumen_uso(res: list[dict]) -> list[dict]:
    return [{"nombre": r["nombre"], "etiqueta": ETIQUETAS.get(r["nombre"], r["nombre"]), "lugar": r.get("lugar"), "ok": r["ok"],
             **({"error": r["error"]} if not r["ok"] else {})} for r in res]
