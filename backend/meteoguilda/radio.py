"""Radioafición: locator Maidenhead, propagación troposférica, condiciones solares, repetidores y cobertura."""
from __future__ import annotations

import asyncio
import csv
import math
import sys
from datetime import datetime, timezone

import httpx

from . import openmeteo
from .db import pool
from .qc import haversine_km

R_EARTH = 6371.0


# ---------- Maidenhead ----------
def to_locator(lat: float, lon: float, chars: int = 6) -> str:
    lon, lat = lon + 180, lat + 90
    f = [int(lon // 20), int(lat // 10)]
    s = chr(65 + f[0]) + chr(65 + f[1])
    lon, lat = lon % 20, lat % 10
    s += str(int(lon // 2)) + str(int(lat // 1))
    if chars >= 6:
        lon, lat = (lon % 2) * 12, (lat % 1) * 24
        s += chr(97 + int(lon)) + chr(97 + int(lat))
    return s


def from_locator(loc: str) -> tuple[float, float]:
    """Centro del cuadro: (lat, lon)."""
    loc = loc.strip()
    if len(loc) not in (4, 6, 8) or not (loc[:2].isalpha() and loc[2:4].isdigit()):
        raise ValueError("Locator inválido")
    u = loc.upper()
    lon = (ord(u[0]) - 65) * 20 - 180 + int(u[2]) * 2
    lat = (ord(u[1]) - 65) * 10 - 90 + int(u[3])
    w, h = 2.0, 1.0
    if len(u) >= 6:
        lon += (ord(u[4]) - 65) * (2 / 24)
        lat += (ord(u[5]) - 65) * (1 / 24)
        w, h = 2 / 24, 1 / 24
    return lat + h / 2, lon + w / 2


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination(lat, lon, az_deg, d_km) -> tuple[float, float]:
    la, lo, a, d = math.radians(lat), math.radians(lon), math.radians(az_deg), d_km / R_EARTH
    la2 = math.asin(math.sin(la) * math.cos(d) + math.cos(la) * math.sin(d) * math.cos(a))
    lo2 = lo + math.atan2(math.sin(a) * math.sin(d) * math.cos(la), math.cos(d) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), (math.degrees(lo2) + 540) % 360 - 180


# ---------- Propagación troposférica (ducting) ----------
def sat_vapor_hpa(t_c: float) -> float:
    return 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))


def refractivity(p_hpa: float, t_c: float, rh: float) -> float:
    t = t_c + 273.15
    e = rh / 100 * sat_vapor_hpa(t_c)
    return 77.6 / t * (p_hpa + 4810 * e / t)


def classify_gradient(g: float) -> str:
    """Gradiente vertical de N (unidades N/km). Estándar ≈ −40."""
    if g <= -157:
        return "conducto"
    if g <= -79:
        return "super-refracción"
    if g > 0:
        return "sub-refracción"
    return "normal"


def propagation_series(d: dict, now: datetime | None = None) -> dict:
    """Gradiente de refractividad entre superficie y 850 hPa (o 700 hPa en montaña)."""
    h = d["hourly"]
    elev = d.get("elevation") or 0
    k0 = openmeteo._start_index(d, now)
    out = []
    for i in range(k0, len(h["time"])):
        try:
            ps, ts, rs = h["surface_pressure"][i], h["temperature_2m"][i], h["relative_humidity_2m"][i]
            lvl = 850 if (h["geopotential_height_850hPa"][i] - elev) >= 500 else 700
            tl, rl, zl = h[f"temperature_{lvl}hPa"][i], h[f"relative_humidity_{lvl}hPa"][i], h[f"geopotential_height_{lvl}hPa"][i]
            ns, nl = refractivity(ps, ts, rs), refractivity(lvl, tl, rl)
            g = (nl - ns) / ((zl - elev) / 1000)
        except (TypeError, KeyError, ZeroDivisionError):
            continue
        out.append({"time": h["time"][i], "gradient": round(g), "cat": classify_gradient(g), "level_hpa": lvl})
    worst = min(out, key=lambda x: x["gradient"]) if out else None
    return {"serie": out, "peor": worst,
            "nota": "Indicador orientativo basado en modelos; los conductos reales dependen de capas finas que "
                    "los modelos resuelven mal (típicas inversiones sobre el mar o en anticiclón)."}


async def propagation(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    hv = ",".join(["temperature_2m", "relative_humidity_2m", "surface_pressure"] +
                  [f"{v}_{p}hPa" for p in (850, 700) for v in ("temperature", "relative_humidity", "geopotential_height")])
    r = await client.get(openmeteo.URL, params={"latitude": lat, "longitude": lon, "hourly": hv, "forecast_days": 3,
                                                "timezone": "auto"}, timeout=30)
    r.raise_for_status()
    return propagation_series(r.json())


# ---------- Condiciones solares/HF (NOAA SWPC) ----------
SWPC = "https://services.swpc.noaa.gov"


async def solar(client: httpx.AsyncClient) -> dict:
    kp = sfi = None
    try:
        r = await client.get(f"{SWPC}/products/noaa-planetary-k-index.json", timeout=15)
        rows = r.json()
        kp = float(rows[-1][1])
    except Exception:  # noqa: BLE001
        pass
    try:
        r = await client.get(f"{SWPC}/products/summary/10cm-flux.json", timeout=15)
        sfi = float(r.json()["Flux"])
    except Exception:  # noqa: BLE001
        pass
    return {"kp": kp, "sfi": sfi, **hf_outlook(kp, sfi), "fuente": "NOAA SWPC"}


def hf_outlook(kp: float | None, sfi: float | None) -> dict:
    notes = []
    if kp is not None:
        notes.append("Tormenta geomagnética: HF degradado, sobre todo en rutas polares" if kp >= 5 else
                     "Geomagnético algo agitado" if kp >= 4 else "Geomagnético tranquilo")
    if sfi is not None:
        notes.append("Flujo solar alto: buenas aperturas en 15, 12 y 10 m" if sfi >= 150 else
                     "Flujo solar moderado: 20 m fiable, 15 m a ratos" if sfi >= 100 else
                     "Flujo solar bajo: mejor 40, 30 y 20 m")
    return {"resumen": notes, "aviso": "Lectura simplificada; para predicción real usa VOACAP/DX Toolbox."}


# ---------- Repetidores ----------
def upsert_repeater(r: dict) -> int:
    with pool().connection() as con:
        row = con.execute(
            "INSERT INTO repeaters (name, callsign, band, freq_mhz, shift_khz, tone_hz, mode, height_m, range_km, source, geom) "
            "VALUES (%(name)s, %(callsign)s, %(band)s, %(freq_mhz)s, %(shift_khz)s, %(tone_hz)s, %(mode)s, %(height_m)s, "
            "%(range_km)s, %(source)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) "
            "ON CONFLICT (callsign, freq_mhz) DO UPDATE SET name = EXCLUDED.name, shift_khz = EXCLUDED.shift_khz, "
            "tone_hz = EXCLUDED.tone_hz, mode = EXCLUDED.mode, height_m = EXCLUDED.height_m, geom = EXCLUDED.geom "
            "RETURNING id", {"band": None, "shift_khz": None, "tone_hz": None, "mode": "FM", "height_m": 10,
                             "range_km": 40, "source": None, "callsign": None, **r}).fetchone()
    return row["id"]


_REP_SEL = ("id, name, callsign, band, freq_mhz, shift_khz, tone_hz, mode, height_m, range_km, "
            "ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon, coverage IS NOT NULL AS has_coverage")


def list_repeaters(bbox: tuple[float, float, float, float] | None = None, band: str | None = None,
                   with_coverage: bool = False) -> list[dict]:
    where, args = ["TRUE"], []
    from .store import clamp_bbox
    bb = clamp_bbox(bbox)
    if bb:
        where.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography")
        args += [bb[1], bb[0], bb[3], bb[2]]
    if band:
        where.append("band = %s")
        args.append(band)
    cov = ", ST_AsGeoJSON(coverage::geometry)::json AS coverage_geojson" if with_coverage else ""
    with pool().connection() as con:
        return con.execute(f"SELECT {_REP_SEL}{cov} FROM repeaters WHERE {' AND '.join(where)} ORDER BY freq_mhz", args).fetchall()


def repeaters_near(lat: float, lon: float, km: float = 60) -> list[dict]:
    with pool().connection() as con:
        return con.execute(
            f"SELECT {_REP_SEL}, ST_Distance(geom, p.g) / 1000 AS dist_km, "
            "COALESCE(ST_Intersects(coverage, p.g), FALSE) AS covers_point FROM repeaters, "
            "(SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) p "
            "WHERE ST_DWithin(geom, p.g, %s) ORDER BY dist_km", [lon, lat, km * 1000]).fetchall()


def repeaters_along(coords: list[tuple[float, float]]) -> list[dict]:
    """Repetidores cuya cobertura estimada intersecta con la ruta, con el % de ruta cubierto."""
    wkt = "LINESTRING(" + ",".join(f"{lo} {la}" for la, lo in coords) + ")"
    with pool().connection() as con:
        return con.execute(
            f"SELECT {_REP_SEL}, 100 * ST_Length(ST_Intersection(coverage::geometry, l.g)::geography) / "
            "NULLIF(ST_Length(l.g::geography), 0) AS pct FROM repeaters, "
            "(SELECT ST_GeomFromText(%s, 4326) AS g) l "
            "WHERE coverage IS NOT NULL AND ST_Intersects(coverage::geometry, l.g) ORDER BY pct DESC", [wkt]).fetchall()


# ---------- Cobertura estimada por línea de vista ----------
def radial_limit(tx_ground: float, h_tx: float, dists_km: list[float], ground: list[float], rx_h: float = 1.5,
                 k: float = 4 / 3, max_gap: int = 2) -> float:
    """Distancia (km) hasta la que hay línea de vista continua (huecos ≤ max_gap muestras) con curvatura 4/3."""
    H = tx_ground + h_tx
    max_ang, last, gap = -1e9, 0.0, 0
    for d_km, e in zip(dists_km, ground):
        d = d_km * 1000
        drop = d / (2 * k * R_EARTH * 1000)
        a_terrain = (e - H) / d - drop
        visible = ((e + rx_h - H) / d - drop) >= max_ang
        max_ang = max(max_ang, a_terrain)
        if visible:
            last, gap = d_km, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    return last


def coverage_ring(lat: float, lon: float, limits_km: list[float]) -> list[tuple[float, float]]:
    n = len(limits_km)
    ring = [destination(lat, lon, 360 * i / n, max(r, 0.3)) for i, r in enumerate(limits_km)]
    return ring + [ring[0]]


async def compute_coverage(client: httpx.AsyncClient, rep_id: int, azimuths: int = 36, step_km: float = 1.0) -> dict:
    with pool().connection() as con:
        rep = con.execute(f"SELECT {_REP_SEL} FROM repeaters WHERE id = %s", [rep_id]).fetchone()
    if not rep:
        raise KeyError(rep_id)
    rng = float(rep["range_km"] or 40)
    dists = [step_km * (j + 1) for j in range(int(rng / step_km))]
    pts = [(rep["lat"], rep["lon"])] + [destination(rep["lat"], rep["lon"], 360 * a / azimuths, d)
                                         for a in range(azimuths) for d in dists]
    elev = await openmeteo.elevations(pts, client)
    tx, rest = elev[0], elev[1:]
    limits = [radial_limit(tx, float(rep["height_m"] or 10), dists, rest[a * len(dists):(a + 1) * len(dists)])
              for a in range(azimuths)]
    ring = coverage_ring(rep["lat"], rep["lon"], limits)
    wkt = "POLYGON((" + ",".join(f"{lo:.5f} {la:.5f}" for la, lo in ring) + "))"
    with pool().connection() as con:
        con.execute("UPDATE repeaters SET coverage = ST_GeogFromText(%s), coverage_at = now() WHERE id = %s", [wkt, rep_id])
    return {"id": rep_id, "radio_medio_km": round(sum(limits) / len(limits), 1), "max_km": round(max(limits), 1)}


# ---------- CLI: importar CSV y calcular coberturas ----------
def _cli(argv: list[str]) -> None:
    from . import db
    db.init_schema()
    if len(argv) >= 2 and argv[0] == "import":
        n = 0
        for row in csv.DictReader(open(argv[1], encoding="utf-8")):
            upsert_repeater({"name": row["name"], "callsign": row.get("callsign") or None, "band": row.get("band") or None,
                             "freq_mhz": float(row["freq_mhz"]), "shift_khz": int(row["shift_khz"]) if row.get("shift_khz") else None,
                             "tone_hz": float(row["tone_hz"]) if row.get("tone_hz") else None, "mode": row.get("mode") or "FM",
                             "height_m": float(row.get("height_m") or 10), "range_km": float(row.get("range_km") or 40),
                             "lat": float(row["lat"]), "lon": float(row["lon"]), "source": row.get("source") or argv[1]})
            n += 1
        print(f"{n} repetidores importados")
    elif argv[:1] == ["coverage"]:
        async def run():
            async with httpx.AsyncClient() as c:
                for r in list_repeaters():
                    if not r["has_coverage"]:
                        print(r["name"], await compute_coverage(c, r["id"]))
                        await asyncio.sleep(2)
        asyncio.run(run())
    else:
        print("Uso: python -m meteoguilda.radio import repetidores.csv | coverage")


if __name__ == "__main__":
    _cli(sys.argv[1:])
