"""Almacén SQLite de observaciones (MVP; migrar a PostGIS/Timescale después)."""
from __future__ import annotations

import os
import sqlite3
import time

from .aprs import WeatherObs

DB_PATH = os.environ.get("MG_DB", "meteoguilda.db")
_FIELDS = ["wind_dir", "wind_ms", "gust_ms", "temp_c", "rain_1h_mm", "rain_24h_mm",
           "humidity", "pressure_hpa"]


def connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE IF NOT EXISTS obs (callsign TEXT, ts REAL, lat REAL, lon REAL, "
        + ", ".join(f"{f} REAL" for f in _FIELDS) + ")")
    con.execute("CREATE INDEX IF NOT EXISTS obs_ts ON obs(ts)")
    con.execute("CREATE INDEX IF NOT EXISTS obs_call ON obs(callsign, ts)")
    return con


def insert(con: sqlite3.Connection, o: WeatherObs, ts: float | None = None) -> None:
    con.execute(
        f"INSERT INTO obs VALUES (?,?,?,?,{','.join('?' * len(_FIELDS))})",
        [o.callsign, ts or time.time(), o.lat, o.lon, *[getattr(o, f) for f in _FIELDS]])


def latest(con: sqlite3.Connection, max_age_s: int = 7200,
           bbox: tuple[float, float, float, float] | None = None) -> list[WeatherObs]:
    """Última observación por estación en la ventana; bbox=(s, w, n, e)."""
    rows = con.execute(
        "SELECT * FROM obs WHERE ts >= ? AND lat IS NOT NULL ORDER BY ts", (time.time() - max_age_s,)
    ).fetchall()
    last: dict[str, sqlite3.Row] = {r["callsign"]: r for r in rows}
    out = []
    for r in last.values():
        if bbox and not (bbox[0] <= r["lat"] <= bbox[2] and bbox[1] <= r["lon"] <= bbox[3]):
            continue
        out.append(WeatherObs(callsign=r["callsign"], lat=r["lat"], lon=r["lon"],
                              **{f: r[f] for f in _FIELDS}))
    return out


def purge(con: sqlite3.Connection, keep_s: int = 7 * 86400) -> None:
    con.execute("DELETE FROM obs WHERE ts < ?", (time.time() - keep_s,))
    con.commit()
