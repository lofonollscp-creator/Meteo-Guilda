"""Observaciones reales de fuentes abiertas SIN clave, además de las balizas APRS:
- METAR de aeropuertos (aviationweather.gov, NOAA) — todo el mundo.
- XEMA del Servei Meteorològic de Catalunya (datos abiertos de Transparència Catalunya) — Cataluña.
Cada lectura sale con el mismo formato que las balizas (id, dist_km, temp_c, viento_kmh…) más `fuente`, `nombre` y `hora` (UTC, ISO).
Las funciones nunca lanzan: ante cualquier fallo de la fuente devuelven [] (el pronóstico no depende de ellas)."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger("sources")
METAR_URL = "https://aviationweather.gov/api/data/metar"
XEMA = "https://analisi.transparenciacatalunya.cat/resource"
UA = {"User-Agent": "MeteoGuilda/1.0 (+https://radio.guildawork.com)"}
TTL = 600
_cache: dict[tuple, tuple[float, list]] = {}
_xema_meta: tuple[float, list[dict]] = (0, [])


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _rh(t: float | None, td: float | None) -> int | None:
    """Humedad relativa (%) a partir de temperatura y punto de rocío (Magnus)."""
    if t is None or td is None:
        return None
    f = lambda x: math.exp(17.625 * x / (243.04 + x))  # noqa: E731
    return max(0, min(100, round(100 * f(td) / f(t))))


async def metar_near(http: httpx.AsyncClient, lat: float, lon: float, km: float = 60) -> list[dict]:
    key = ("metar", round(lat, 1), round(lon, 1), round(km))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return _filter(hit[1], lat, lon, km)
    d = km / 111.0
    dlon = d / max(math.cos(math.radians(lat)), 0.2)
    try:
        r = await http.get(METAR_URL, params={"bbox": f"{lat - d:.3f},{lon - dlon:.3f},{lat + d:.3f},{lon + dlon:.3f}", "format": "json"},
                           headers=UA, timeout=8)
        r.raise_for_status()
        rows = r.json() if r.content else []
    except Exception as e:  # noqa: BLE001
        log.warning("METAR: %s", e)
        return []
    out = []
    for m in rows:
        if m.get("lat") is None or m.get("temp") is None:
            continue
        out.append({"id": m["icaoId"], "nombre": (m.get("name") or "").split(",")[0], "lat": m["lat"], "lon": m["lon"], "fuente": "METAR",
                    "temp_c": m["temp"], "viento_kmh": round(m["wspd"] * 1.852) if isinstance(m.get("wspd"), (int, float)) else None,
                    "racha_kmh": round(m["wgst"] * 1.852) if isinstance(m.get("wgst"), (int, float)) else None,
                    "humedad_pct": _rh(m.get("temp"), m.get("dewp")), "presion_hpa": m.get("altim"),
                    "hora": (m.get("reportTime") or "")[:19] + "Z" if m.get("reportTime") else None})
    _cache[key] = (time.time(), out)
    return _filter(out, lat, lon, km)


def _filter(rows: list[dict], lat: float, lon: float, km: float) -> list[dict]:
    res = []
    for o in rows:
        dist = haversine(lat, lon, o["lat"], o["lon"])
        if dist <= km:
            res.append({**o, "dist_km": round(dist)})
    return sorted(res, key=lambda o: o["dist_km"])


async def _xema_stations(http: httpx.AsyncClient) -> list[dict]:
    global _xema_meta
    if time.time() - _xema_meta[0] < 86400 and _xema_meta[1]:
        return _xema_meta[1]
    r = await http.get(f"{XEMA}/yqwd-vj5e.json", params={"$limit": 2000, "$select": "codi_estacio,nom_estacio,latitud,longitud"},
                       headers=UA, timeout=10)
    r.raise_for_status()
    st = [{"codi": s["codi_estacio"], "nom": s["nom_estacio"], "lat": float(s["latitud"]), "lon": float(s["longitud"])}
          for s in r.json() if s.get("latitud") and s.get("longitud")]
    _xema_meta = (time.time(), st)
    return st


async def xema_near(http: httpx.AsyncClient, lat: float, lon: float, km: float = 40, n: int = 4) -> list[dict]:
    if not (40.4 <= lat <= 43.0 and 0.0 <= lon <= 3.5):   # fuera de Cataluña ni se consulta
        return []
    key = ("xema", round(lat, 2), round(lon, 2), round(km))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        near = sorted(((haversine(lat, lon, s["lat"], s["lon"]), s) for s in await _xema_stations(http)), key=lambda x: x[0])
        near = [(d, s) for d, s in near[:12] if d <= km]
        if not near:
            return []
        codes = ",".join(f"'{s['codi']}'" for _, s in near)
        since = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S")
        r = await http.get(f"{XEMA}/nzvn-apee.json", params={
            "$where": f"codi_estacio in ({codes}) AND codi_variable in ('30','31','32','33','34','35','50') AND data_lectura > '{since}'",
            "$order": "data_lectura DESC", "$limit": 800, "$select": "codi_estacio,codi_variable,data_lectura,valor_lectura"},
            headers=UA, timeout=12)
        r.raise_for_status()
        last: dict[tuple[str, str], tuple[str, float]] = {}
        lluvia: dict[str, list[tuple[str, float]]] = {}      # lecturas de 30 min de precipitación, de la más reciente a la más antigua
        for x in r.json():
            k = (x["codi_estacio"], x["codi_variable"])
            if x["codi_variable"] == "35":
                lluvia.setdefault(x["codi_estacio"], []).append((x["data_lectura"], float(x["valor_lectura"])))
            if k not in last:
                last[k] = (x["data_lectura"], float(x["valor_lectura"]))
    except Exception as e:  # noqa: BLE001
        log.warning("XEMA: %s", e)
        return []
    out = []
    for dist, s in near:
        g = lambda v: last.get((s["codi"], v), (None, None))  # noqa: E731
        t, vv, rf, hr, pr, dv = g("32"), g("30"), g("50"), g("33"), g("34"), g("31")
        if t[1] is None:
            continue
        ll = lluvia.get(s["codi"], [])
        mm = None                                            # litros/m² en la última hora (dos lecturas de 30 min); solo si la estación midió lluvia reciente
        if ll and ll[0][0][:13] >= t[0][:13]:
            mm = round(sum(v for d, v in ll[:2] if d >= (datetime.fromisoformat(ll[0][0]) - timedelta(minutes=45)).isoformat()), 1)
        out.append({"id": s["codi"], "nombre": s["nom"], "lat": s["lat"], "lon": s["lon"], "dist_km": round(dist), "fuente": "XEMA",
                    "temp_c": t[1], "viento_kmh": round(vv[1] * 3.6) if vv[1] is not None else None,
                    "racha_kmh": round(rf[1] * 3.6) if rf[1] is not None else None, "humedad_pct": round(hr[1]) if hr[1] is not None else None,
                    "presion_hpa": pr[1], "dir_viento_deg": round(dv[1]) if dv[1] is not None else None, "lluvia_1h_mm": mm, "hora": t[0][:19] + "Z"})
    out = out[:n]
    _cache[key] = (time.time(), out)
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    return out


async def nearby(http: httpx.AsyncClient, lat: float, lon: float, km: float = 40) -> list[dict]:
    """METAR + XEMA cercanos, de más a menos cerca."""
    res = await asyncio.gather(metar_near(http, lat, lon, km), xema_near(http, lat, lon, km), return_exceptions=True)
    rows = [o for r in res if isinstance(r, list) for o in r]
    return sorted(rows, key=lambda o: o["dist_km"])
