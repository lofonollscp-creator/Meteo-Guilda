"""Estado del mar (Open-Meteo Marine, sin clave): oleaje, mar de fondo, periodo, dirección y temperatura del agua.
Solo hay datos cerca de la costa: tierra adentro devuelve todo nulo y se informa como «sin mar»."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

URL = "https://marine-api.open-meteo.com/v1/marine"
VARS = ["wave_height", "wave_period", "wave_direction", "swell_wave_height", "swell_wave_period", "sea_surface_temperature"]
# Escala Douglas simplificada por altura significativa de ola (m): (límite superior, nombre, nivel 0-2)
DOUGLAS = [(0.1, "calma", 0), (0.5, "rizada", 0), (1.25, "marejadilla", 0), (2.5, "marejada", 1), (4.0, "fuerte marejada", 2),
           (6.0, "gruesa", 2), (9.0, "muy gruesa", 2), (14.0, "arbolada", 2), (1e9, "montañosa", 2)]
_cache: dict[tuple, tuple[float, dict]] = {}


def estado(h: float) -> tuple[str, int]:
    return next((n, lv) for lim, n, lv in DOUGLAS if h < lim)


async def fetch(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = (round(lat, 1), round(lon, 1))
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    r = await http.get(URL, params={"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(VARS), "forecast_days": 3, "timezone": "auto"})
    r.raise_for_status()
    out = summarize(r.json(), lat, lon)
    _cache[k] = (time.time(), out)
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    return out


def summarize(d: dict, lat: float, lon: float, now: datetime | None = None) -> dict:
    h = d.get("hourly") or {}
    times = h.get("time") or []
    waves = h.get("wave_height") or []
    if not times or not any(v is not None for v in waves):
        return {"ubicacion": {"lat": lat, "lon": lon}, "hay_mar": False, "horas": [],
                "nota": "Sin datos de oleaje en este punto: está tierra adentro o demasiado lejos de la costa."}
    local = ((now or datetime.now(timezone.utc)) + timedelta(seconds=d.get("utc_offset_seconds", 0))).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    cur = local.strftime("%Y-%m-%dT%H:%M")
    i0 = next((i for i, t in enumerate(times) if t >= cur), 0)
    g = lambda var, i: (h.get(var) or [None] * len(times))[i]  # noqa: E731
    horas = []
    for i in range(i0, min(i0 + 48, len(times))):
        hs = g("wave_height", i)
        horas.append({"hora": times[i], "ola_m": hs, "periodo_s": g("wave_period", i), "dir": g("wave_direction", i),
                      "fondo_m": g("swell_wave_height", i), "fondo_periodo_s": g("swell_wave_period", i), "agua_c": g("sea_surface_temperature", i),
                      "estado": estado(hs)[0] if hs is not None else None})
    ahora = horas[0]
    vals = [x["ola_m"] for x in horas if x["ola_m"] is not None]
    pico = max(horas, key=lambda x: x["ola_m"] if x["ola_m"] is not None else -1)
    nombre, nivel = estado(ahora["ola_m"]) if ahora["ola_m"] is not None else ("sin dato", 0)
    nombre_pico, nivel_pico = estado(max(vals)) if vals else ("sin dato", 0)
    return {"ubicacion": {"lat": lat, "lon": lon}, "hay_mar": True, "ahora": {**ahora, "estado": nombre, "nivel": nivel},
            "maximo_48h": {"ola_m": max(vals) if vals else None, "hora": pico["hora"], "estado": nombre_pico, "nivel": nivel_pico},
            "horas": horas,
            "nota": "Altura significativa de la ola (la mitad de las olas, más bajas; las mayores pueden duplicarla). Modelo en malla de ~5-25 km: "
                    "cerca de puertos y calas la mar real puede diferir. Estado según escala Douglas. No sustituye al parte oficial de AEMET/Salvamento Marítimo."}
