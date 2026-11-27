"""Planificador de rutas: trazado (Valhalla/GPX), perfil, tiempos, meteo a la hora de paso y riesgo."""
from __future__ import annotations

import bisect
import math
import os
from datetime import datetime, timedelta, timezone

import httpx
from defusedxml import ElementTree as ET

from . import openmeteo
from .qc import haversine_km

VALHALLA = os.environ.get("MG_VALHALLA_URL", "https://valhalla1.openstreetmap.de")
COSTING = {"foot": "pedestrian", "car": "auto"}
LEVEL_TXT = ["Favorable", "Precaución", "Desaconsejable"]


# ---------- geometría ----------
def decode_polyline(s: str, precision: int = 6) -> list[tuple[float, float]]:
    """Polyline de Valhalla (precisión 6) -> [(lat, lon)]."""
    out, i, lat, lon, f = [], 0, 0, 0, 10 ** precision
    while i < len(s):
        for axis in (0, 1):
            shift = res = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if axis == 0:
                lat += d
            else:
                lon += d
        out.append((lat / f, lon / f))
    return out


def parse_gpx(text: str, max_points: int = 3000) -> list[tuple[float, float]]:
    root = ET.fromstring(text.encode() if isinstance(text, str) else text)
    pts = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept") and "lat" in el.attrib and "lon" in el.attrib:
            pts.append((float(el.attrib["lat"]), float(el.attrib["lon"])))
    if len(pts) < 2:
        raise ValueError("El GPX no contiene una ruta o track válido")
    if len(pts) > max_points:
        step = math.ceil(len(pts) / max_points)
        pts = pts[::step] + ([pts[-1]] if pts[-1] != pts[::step][-1] else [])
    return pts


def cumulative_km(coords: list[tuple[float, float]]) -> list[float]:
    cum = [0.0]
    for a, b in zip(coords, coords[1:]):
        cum.append(cum[-1] + haversine_km(a[0], a[1], b[0], b[1]))
    return cum


def point_at(coords, cum, d_km: float) -> tuple[float, float]:
    d_km = min(max(d_km, 0), cum[-1])
    i = max(1, bisect.bisect_left(cum, d_km))
    i = min(i, len(coords) - 1)
    seg = cum[i] - cum[i - 1] or 1e-9
    f = (d_km - cum[i - 1]) / seg
    return (coords[i - 1][0] + f * (coords[i][0] - coords[i - 1][0]),
            coords[i - 1][1] + f * (coords[i][1] - coords[i - 1][1]))


def thin(coords, n: int = 700):
    if len(coords) <= n:
        return coords
    step = len(coords) / n
    out = [coords[int(i * step)] for i in range(n)]
    return out + [coords[-1]]


# ---------- perfil y tiempos ----------
def profile_stats(d_km: list[float], ele: list[float], smooth_m: float = 3.0) -> tuple[float, float]:
    """Ascenso/descenso acumulado (m) ignorando ruido < smooth_m."""
    asc = desc = 0.0
    ref = ele[0]
    for e in ele[1:]:
        if e - ref >= smooth_m:
            asc += e - ref
            ref = e
        elif ref - e >= smooth_m:
            desc += ref - e
            ref = e
    return round(asc), round(desc)


def naismith_minutes(d_km: list[float], ele: list[float]) -> list[float]:
    """Minutos acumulados en cada punto del perfil: 5 km/h + 1 h por cada 600 m de ascenso."""
    t = [0.0]
    for i in range(1, len(d_km)):
        dd = d_km[i] - d_km[i - 1]
        up = max(0.0, ele[i] - ele[i - 1])
        t.append(t[-1] + dd / 5 * 60 + up / 600 * 60)
    return t


# ---------- luz solar (ecuación de salida/puesta del sol) ----------
def _jd(dt: datetime) -> float:
    return dt.timestamp() / 86400 + 2440587.5


def is_daylight(dt: datetime, lat: float, lon: float) -> bool:
    jd = _jd(dt)
    for dj in (-1, 0, 1):
        n = math.ceil(jd - 2451545.0 + 0.0008) + dj
        js = n - lon / 360
        m = (357.5291 + 0.98560028 * js) % 360
        mr = math.radians(m)
        c = 1.9148 * math.sin(mr) + 0.02 * math.sin(2 * mr) + 0.0003 * math.sin(3 * mr)
        lam = math.radians((m + c + 180 + 102.9372) % 360)
        transit = 2451545.0 + js + 0.0053 * math.sin(mr) - 0.0069 * math.sin(2 * lam)
        dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
        cosw = (math.sin(math.radians(-0.833)) - math.sin(math.radians(lat)) * math.sin(dec)) / (
            math.cos(math.radians(lat)) * math.cos(dec))
        if cosw <= -1:      # sol de medianoche
            return True
        if cosw >= 1:       # noche polar
            continue
        w = math.degrees(math.acos(cosw)) / 360
        if transit - w <= jd <= transit + w:
            return True
    return False


# ---------- riesgo ----------
# (ámbar, rojo) por modo
T = {
    "foot": {"gust": (45, 70), "wind": (30, 50), "rain": (1.0, 4.0), "snow": (0.3, 2.0), "heat": (30, 36), "cold": (-3, -10)},
    "car": {"gust": (60, 90), "wind": (999, 999), "rain": (2.0, 6.0), "snow": (0.3, 1.5), "heat": (999, 999), "cold": (-99, -99)},
}


def assess(mode: str, w: dict, daylight: bool = True) -> tuple[int, list[str]]:
    """Nivel (0 verde, 1 ámbar, 2 rojo) y motivos para unas condiciones horarias."""
    t = T[mode]
    lvl, why = 0, []

    def bump(level: int, text: str):
        nonlocal lvl
        lvl = max(lvl, level)
        why.append(text)

    gust, wind = (w.get("gust") or 0) * 3.6, (w.get("wind") or 0) * 3.6
    rain, snow, temp, cape = w.get("rain") or 0, w.get("snow") or 0, w.get("temp"), w.get("cape") or 0
    if gust >= t["gust"][1]:
        bump(2, f"Rachas de {round(gust)} km/h")
    elif gust >= t["gust"][0]:
        bump(1, f"Rachas de {round(gust)} km/h")
    if wind >= t["wind"][1]:
        bump(2, f"Viento sostenido de {round(wind)} km/h")
    elif wind >= t["wind"][0]:
        bump(1, f"Viento sostenido de {round(wind)} km/h")
    if rain >= t["rain"][1]:
        bump(2, f"Lluvia fuerte ({rain:.1f} mm/h)")
    elif rain >= t["rain"][0]:
        bump(1, f"Lluvia ({rain:.1f} mm/h)")
    if snow >= t["snow"][1]:
        bump(2, f"Nevada intensa ({snow:.1f} cm/h)")
    elif snow >= t["snow"][0]:
        bump(1, f"Nieve ({snow:.1f} cm/h)")
    if temp is not None:
        if temp >= t["heat"][1]:
            bump(2, f"Calor extremo ({round(temp)} °C)")
        elif temp >= t["heat"][0]:
            bump(1, f"Calor ({round(temp)} °C)")
        if temp <= t["cold"][1]:
            bump(2, f"Frío extremo ({round(temp)} °C)")
        elif temp <= t["cold"][0]:
            bump(1, f"Frío ({round(temp)} °C)")
        if temp <= 1 and rain + snow >= 0.1:
            bump(2 if (mode == "car" and temp <= 0) else 1, "Posible hielo o nieve en el suelo")
    if cape >= 800 and rain >= 0.3:
        bump(2 if cape >= 1800 else 1, "Riesgo de tormentas")
    if mode == "foot" and not daylight:
        bump(1, "Tramo de noche")
    return lvl, why


def _utc_times(h: dict) -> list[datetime]:
    off = timedelta(seconds=h["utc_offset"])
    return [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - off for t in h["time"]]


def at(h: dict, t: datetime, utc_times: list[datetime]) -> dict | None:
    """Valores del consenso horario más cercano a `t` (UTC); None si queda fuera de la serie."""
    if not utc_times or t < utc_times[0] - timedelta(minutes=30) or t > utc_times[-1] + timedelta(minutes=30):
        return None
    i = min(range(len(utc_times)), key=lambda k: abs((utc_times[k] - t).total_seconds())) \
        if len(utc_times) < 8 else _nearest(utc_times, t)
    g = lambda k: h[k][i] if h.get(k) else None  # noqa: E731
    return {"temp": g("temperature_2m"), "rain": g("precipitation"), "wind": g("wind_speed_10m"),
            "gust": g("wind_gusts_10m"), "dir": g("wind_direction_10m"), "cloud": g("cloud_cover"),
            "snow": g("snowfall"), "cape": g("cape"), "iso": g("freezing_level_height"), "rh": g("relative_humidity_2m"), "time": h["time"][i]}


def _nearest(ts: list[datetime], t: datetime) -> int:
    i = bisect.bisect_left(ts, t)
    if i == 0:
        return 0
    if i == len(ts):
        return len(ts) - 1
    return i if (ts[i] - t) < (t - ts[i - 1]) else i - 1


def evaluate(mode: str, samples: list[dict], hourly: list[dict], depart: datetime) -> dict | None:
    """Nivel por muestra para una salida concreta. None si algún ETA queda fuera de los datos."""
    out, level, score = [], 0, 0
    cache = _cache_times(hourly)
    for s, h, ut in zip(samples, hourly, cache):
        t = depart + timedelta(minutes=s["eta_min"])
        w = at(h, t, ut)
        if w is None:
            return None
        lvl, why = assess(mode, w, is_daylight(t, s["lat"], s["lon"]))
        out.append({"t": t.isoformat(), "level": lvl, "why": why, "w": w})
        level = max(level, lvl)
        score += lvl * lvl + 0.1 * lvl
    return {"level": level, "score": round(score, 2), "samples": out}


_ut_cache: dict[int, list[datetime]] = {}


def _cache_times(hourly: list[dict]) -> list[list[datetime]]:
    res = []
    for h in hourly:
        k = id(h)
        if k not in _ut_cache or len(_ut_cache) > 256:
            if len(_ut_cache) > 256:
                _ut_cache.clear()
            _ut_cache[k] = _utc_times(h)
        res.append(_ut_cache[k])
    return res


def best_windows(mode: str, samples, hourly, start: datetime, hours: int = 36, top: int = 3) -> list[dict]:
    cands = []
    for k in range(hours):
        dep = start + timedelta(hours=k)
        ev = evaluate(mode, samples, hourly, dep)
        if ev:
            motivos = sorted({m for s in ev["samples"] for m in s["why"]})
            cands.append({"salida": dep.isoformat(), "nivel": ev["level"], "puntuacion": ev["score"], "motivos": motivos})
    cands.sort(key=lambda c: (c["puntuacion"], c["salida"]))
    chosen: list[dict] = []
    for c in cands:
        if all(abs((datetime.fromisoformat(c["salida"]) - datetime.fromisoformat(x["salida"])).total_seconds()) >= 7200
               for x in chosen):
            chosen.append(c)
        if len(chosen) == top:
            break
    return sorted(chosen, key=lambda c: c["salida"])


# ---------- trazado ----------
async def valhalla_route(waypoints: list[tuple[float, float]], mode: str, client: httpx.AsyncClient
                         ) -> tuple[list[tuple[float, float]], float | None]:
    body = {"locations": [{"lat": la, "lon": lo, "type": "break"} for la, lo in waypoints],
            "costing": COSTING[mode], "directions_options": {"units": "kilometers"}}
    r = await client.post(f"{VALHALLA}/route", json=body, timeout=40)
    if r.status_code >= 400:
        raise RuntimeError(r.json().get("error", f"Valhalla {r.status_code}") if r.headers.get("content-type", "")
                           .startswith("application/json") else f"Valhalla {r.status_code}")
    trip = r.json()["trip"]
    coords: list[tuple[float, float]] = []
    for leg in trip["legs"]:
        pts = decode_polyline(leg["shape"])
        coords += pts[1:] if coords else pts
    return coords, trip["summary"].get("time")


async def plan(client: httpx.AsyncClient, *, waypoints: list[tuple[float, float]] | None, gpx: str | None,
               mode: str, depart: datetime | None, now: datetime | None = None) -> dict:
    if mode not in COSTING:
        raise ValueError("Modo no soportado (foot|car)")
    now = now or datetime.now(timezone.utc)
    route_secs = None
    if gpx:
        coords = parse_gpx(gpx)
    elif waypoints and len(waypoints) >= 2:
        coords, route_secs = await valhalla_route(waypoints, mode, client)
    else:
        raise ValueError("Indica al menos origen y destino, o un GPX")
    cum = cumulative_km(coords)
    total = cum[-1]
    if total < 0.05:
        raise ValueError("La ruta es demasiado corta")

    # perfil de elevación (≤ 80 puntos) y tiempo
    n_prof = min(80, max(2, int(total / 0.25) + 1))
    prof_d = [total * i / (n_prof - 1) for i in range(n_prof)]
    prof_pts = [point_at(coords, cum, d) for d in prof_d]
    try:
        ele = await openmeteo.elevations(prof_pts, client)
    except (httpx.HTTPError, KeyError):
        ele = []
    asc = desc = 0
    if ele:
        asc, desc = profile_stats(prof_d, ele)
    if mode == "foot" and ele:
        tmin = naismith_minutes(prof_d, ele)
        total_min = tmin[-1]
        t_at = lambda d: _interp(d, prof_d, tmin)  # noqa: E731
    else:
        total_min = (route_secs / 60) if route_secs else total / (5 if mode == "foot" else 60) * 60
        t_at = lambda d: total_min * d / total  # noqa: E731

    # muestreo para la meteo
    step_km = 2.5 if mode == "foot" else 12
    n = max(4, min(12, round(total / step_km) + 1))
    samples = []
    for i in range(n):
        d = total * i / (n - 1)
        la, lo = point_at(coords, cum, d)
        samples.append({"lat": round(la, 5), "lon": round(lo, 5), "d_km": round(d, 2), "eta_min": round(t_at(d), 1)})
    hourly_raw = await openmeteo.fetch_many([(s["lat"], s["lon"]) for s in samples], days=3, client=client)
    hourly = [openmeteo.hourly_consensus(d, now) for d in hourly_raw]
    for s, h in zip(samples, hourly):
        s["ele"] = round(h["elevation"]) if h.get("elevation") is not None else None

    start = depart or (now + timedelta(minutes=10))
    ev = evaluate(mode, samples, hourly, start)
    windows_from = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    windows = best_windows(mode, samples, hourly, windows_from)
    out_samples = []
    for k, s in enumerate(samples):
        row = dict(s)
        if ev:
            e = ev["samples"][k]
            w = e["w"]
            row.update({"t": e["t"], "nivel": e["level"], "motivos": e["why"],
                        "meteo": {"temp_c": _r(w["temp"]), "viento_kmh": _r(w["wind"], 3.6), "racha_kmh": _r(w["gust"], 3.6),
                                  "viento_dir": w["dir"], "lluvia_mm": _r(w["rain"], 1, 1), "nieve_cm": _r(w["snow"], 1, 1),
                                  "nubes_pct": _r(w["cloud"]), "cape_jkg": _r(w["cape"]), "isocero_m": _r(w["iso"]), "humedad_pct": _r(w.get("rh"))}})
        out_samples.append(row)
    return {
        "modo": mode, "distancia_km": round(total, 1), "duracion_min": round(total_min), "ascenso_m": asc, "descenso_m": desc,
        "salida": start.isoformat(), "nivel_global": ev["level"] if ev else None,
        "nivel_texto": LEVEL_TXT[ev["level"]] if ev else "Sin datos para esa hora (máx. 72 h)",
        "geometria": [[round(lo, 5), round(la, 5)] for la, lo in thin(coords)],
        "perfil": [{"d_km": round(d, 2), "ele": round(e)} for d, e in zip(prof_d, ele)] if ele else [],
        "muestras": out_samples, "ventanas": windows,
    }


def _interp(x, xs, ys):
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    f = (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1e-9)
    return ys[i - 1] + f * (ys[i] - ys[i - 1])


def _r(v, mult: float = 1, nd: int = 0):
    return None if v is None else (round(v * mult, nd) if nd else round(v * mult))
