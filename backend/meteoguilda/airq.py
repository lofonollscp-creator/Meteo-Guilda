"""Calidad del aire, índice UV y polen (Open-Meteo Air Quality, sin clave). El polen solo existe en Europa y en temporada."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
VARS = ["european_aqi", "pm10", "pm2_5", "ozone", "nitrogen_dioxide", "uv_index", "alder_pollen", "birch_pollen", "grass_pollen",
        "mugwort_pollen", "olive_pollen", "ragweed_pollen"]
POLLEN = {"alder_pollen": "aliso", "birch_pollen": "abedul", "grass_pollen": "gramíneas", "mugwort_pollen": "artemisa",
          "olive_pollen": "olivo", "ragweed_pollen": "ambrosía"}
EAQI = [(20, "buena"), (40, "aceptable"), (60, "moderada"), (80, "mala"), (100, "muy mala"), (1e9, "extremadamente mala")]
UV = [(3, "bajo"), (6, "moderado"), (8, "alto"), (11, "muy alto"), (1e9, "extremo")]
POL = [(1, "sin polen"), (10, "bajo"), (50, "moderado"), (200, "alto"), (1e9, "muy alto")]
UV_ADVICE = {"bajo": "Sin protección especial.", "moderado": "Gafas y crema si estás mucho rato al sol.", "alto": "Crema, gafas y sombrero; evita las horas centrales.",
             "muy alto": "Protección extra y evita el sol del mediodía.", "extremo": "Evita el sol directo; protección máxima."}
_cache: dict[tuple, tuple[float, dict]] = {}


def _cat(table, v):
    return next(t for lim, t in table if v < lim)


async def fetch(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = (round(lat, 1), round(lon, 1))
    hit = _cache.get(k)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        r = await http.get(URL, params={"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(VARS), "forecast_days": 2, "timezone": "auto"})
        r.raise_for_status()
    except httpx.HTTPError:
        if hit and time.time() - hit[0] < 6 * 3600:      # la fuente limita o falla: mejor datos de hace unas horas, avisando, que un error
            return {**hit[1], "desactualizado": True}
        raise
    out = summarize(r.json(), lat, lon)
    _cache[k] = (time.time(), out)
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    return out


def summarize(d: dict, lat: float, lon: float, now: datetime | None = None) -> dict:
    h = d["hourly"]
    local = ((now or datetime.now(timezone.utc)) + timedelta(seconds=d.get("utc_offset_seconds", 0))).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    cur = local.strftime("%Y-%m-%dT%H:%M")
    i0 = next((i for i, t in enumerate(h["time"]) if t >= cur), 0)
    g = lambda var, i: (h.get(var) or [None] * len(h["time"]))[i]  # noqa: E731
    aqi, uv = g("european_aqi", i0), g("uv_index", i0)
    actual = {"hora": h["time"][i0], "aqi": aqi, "aqi_texto": _cat(EAQI, aqi) if aqi is not None else None, "pm2_5": g("pm2_5", i0), "pm10": g("pm10", i0),
              "ozono": g("ozone", i0), "no2": g("nitrogen_dioxide", i0), "uv": uv, "uv_texto": _cat(UV, uv) if uv is not None else None}
    today = h["time"][i0][:10]
    uvs = [(x, h["time"][i]) for i, x in enumerate(h.get("uv_index") or []) if x is not None and h["time"][i][:10] == today]
    uv_max = max(uvs) if uvs else None
    horas = [{"hora": h["time"][i], "aqi": g("european_aqi", i), "uv": g("uv_index", i)} for i in range(i0, min(i0 + 24, len(h["time"])))]
    pollen = []
    for var, name in POLLEN.items():
        vals = [x for x in (h.get(var) or [])[i0:i0 + 24] if x is not None]
        if vals:
            m = max(vals)
            pollen.append({"tipo": name, "max_24h": round(m, 1), "nivel": _cat(POL, m)})
    pollen.sort(key=lambda p: -p["max_24h"])
    return {"ubicacion": {"lat": lat, "lon": lon}, "actual": actual,
            "uv_max_hoy": {"valor": round(uv_max[0], 1), "hora": uv_max[1][11:16], "texto": _cat(UV, uv_max[0]), "consejo": UV_ADVICE[_cat(UV, uv_max[0])]} if uv_max else None,
            "polen": pollen, "horas": horas,
            "nota": "Índice europeo de calidad del aire (EAQI), UV y polen (granos/m³; solo Europa y en temporada). Modelo CAMS; son estimaciones, no mediciones."}
