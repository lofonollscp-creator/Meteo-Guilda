"""Observaciones APRS en PostGIS."""
from __future__ import annotations

from typing import Iterable

from .aprs import WeatherObs
from .db import pool

_F = ["wind_dir", "wind_ms", "gust_ms", "temp_c", "rain_1h_mm", "rain_24h_mm", "humidity", "pressure_hpa"]
_SEL = ("callsign, ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon, " + ", ".join(_F))


def clamp_bbox(bbox):
    """(sur, oeste, norte, este) acotado; None si abarca ≥179° (PostGIS rechaza aristas antipodales)."""
    if not bbox:
        return None
    s, w, n, e = bbox
    if e - w >= 179 or n - s >= 179:
        return None
    return max(s, -89.9), max(w, -179.9), min(n, 89.9), min(e, 179.9)


def _row(r: dict) -> WeatherObs:
    return WeatherObs(callsign=r["callsign"], lat=r["lat"], lon=r["lon"],
                      **{f: (None if r[f] is None else r[f]) for f in _F})


def insert_many(items: Iterable[WeatherObs]) -> int:
    rows = [(o.callsign, o.lon, o.lat, *[getattr(o, f) for f in _F]) for o in items if o.lat is not None]
    if not rows:
        return 0
    with pool().connection() as con, con.cursor() as cur:
        cur.executemany(
            f"INSERT INTO obs (callsign, geom, {', '.join(_F)}) VALUES "
            f"(%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, {', '.join(['%s'] * len(_F))})", rows)
    return len(rows)


def latest(max_age_s: int = 7200, bbox: tuple[float, float, float, float] | None = None) -> list[WeatherObs]:
    """Última observación por estación; bbox = (sur, oeste, norte, este)."""
    where, args = "ts > now() - make_interval(secs => %s)", [max_age_s]
    bb = clamp_bbox(bbox)
    if bb:
        where += " AND geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography"
        args += [bb[1], bb[0], bb[3], bb[2]]
    with pool().connection() as con:
        rows = con.execute(f"SELECT DISTINCT ON (callsign) {_SEL} FROM obs WHERE {where} "
                           "ORDER BY callsign, ts DESC, id DESC", args).fetchall()
    return [_row(r) for r in rows]


def nearby(lat: float, lon: float, radius_km: float = 40, max_age_s: int = 10800) -> list[tuple[float, WeatherObs]]:
    """(distancia_km, obs) ordenadas, usando índice GiST (ST_DWithin)."""
    with pool().connection() as con:
        rows = con.execute(
            f"SELECT * FROM (SELECT DISTINCT ON (callsign) {_SEL}, "
            "ST_Distance(geom, p.g) / 1000 AS dist FROM obs, "
            "(SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g) p "
            "WHERE ts > now() - make_interval(secs => %s) AND ST_DWithin(geom, p.g, %s) "
            "ORDER BY callsign, ts DESC, id DESC) t ORDER BY dist",
            [lon, lat, max_age_s, radius_km * 1000]).fetchall()
    return [(r["dist"], _row(r)) for r in rows]


def near_line(coords: list[tuple[float, float]], radius_km: float = 5, max_age_s: int = 10800
              ) -> list[tuple[float, WeatherObs]]:
    """Estaciones a menos de `radius_km` de una línea [(lat, lon), ...]."""
    wkt = "LINESTRING(" + ",".join(f"{lon} {lat}" for lat, lon in coords) + ")"
    with pool().connection() as con:
        rows = con.execute(
            f"SELECT * FROM (SELECT DISTINCT ON (callsign) {_SEL}, "
            "ST_Distance(geom, l.g) / 1000 AS dist FROM obs, "
            "(SELECT ST_GeogFromText(%s) AS g) l "
            "WHERE ts > now() - make_interval(secs => %s) AND ST_DWithin(geom, l.g, %s) "
            "ORDER BY callsign, ts DESC, id DESC) t ORDER BY dist",
            [wkt, max_age_s, radius_km * 1000]).fetchall()
    return [(r["dist"], _row(r)) for r in rows]


def purge(keep_s: int = 7 * 86400) -> None:
    with pool().connection() as con:
        con.execute("DELETE FROM obs WHERE ts < now() - make_interval(secs => %s)", [keep_s])
