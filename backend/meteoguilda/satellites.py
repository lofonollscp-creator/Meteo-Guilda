"""Pasos de satélites de aficionados (y la ISS) con SGP4 y TLE de CelesTrak."""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import httpx
from sgp4.api import Satrec, jday

from .db import pool

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
SOURCES = {"amateur": {"GROUP": "amateur", "FORMAT": "tle"}, "stations": {"GROUP": "stations", "FORMAT": "tle"}}
TTL = 12 * 3600
# Datos conocidos y estables de la ISS (Región 1 IARU); verifica siempre en ariss.org: pueden cambiar.
ISS_INFO = {"aprs": "145,825 MHz (digipetidor APRS)", "voz": "bajada 145,800 MHz FM; subida 145,200 MHz (Región 1)"}
WGS_A, WGS_F = 6378.137, 1 / 298.257223563
_mem: dict[str, tuple[float, str]] = {}


def parse_tles(text: str) -> list[dict]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            out.append({"name": name, "norad": int(l1[2:7]), "l1": l1, "l2": l2})
    return out


async def load_tles(client: httpx.AsyncClient) -> list[dict]:
    """TLE de aficionados + estaciones, con caché en memoria y base de datos (sirve la copia vieja si falla la red)."""
    texts = []
    for key, params in SOURCES.items():
        hit = _mem.get(key)
        if not (hit and time.time() - hit[0] < TTL):
            try:
                r = await client.get(CELESTRAK, params=params, timeout=30)
                r.raise_for_status()
                hit = (time.time(), r.text)
                _mem[key] = hit
                _db_put(f"tle:{key}", r.text)
            except (httpx.HTTPError, OSError):
                hit = hit or (0, _db_get(f"tle:{key}") or "")
                _mem[key] = hit
        texts.append(hit[1])
    seen, out = set(), []
    for t in texts:
        for s in parse_tles(t):
            if s["norad"] not in seen:
                seen.add(s["norad"]); out.append(s)
    return out


def _db_put(k: str, v: str) -> None:
    try:
        with pool().connection() as con:
            con.execute("INSERT INTO kv_cache (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = now()", [k, v])
    except Exception:  # noqa: BLE001
        pass


def _db_get(k: str) -> str | None:
    try:
        with pool().connection() as con:
            r = con.execute("SELECT v FROM kv_cache WHERE k = %s", [k]).fetchone()
        return r["v"] if r else None
    except Exception:  # noqa: BLE001
        return None


def _gmst(jd_ut1: float) -> float:
    d = jd_ut1 - 2451545.0
    return math.radians((280.46061837 + 360.98564736629 * d) % 360)


def observer_ecef(lat: float, lon: float, alt_m: float = 0) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    e2 = WGS_F * (2 - WGS_F)
    n = WGS_A / math.sqrt(1 - e2 * math.sin(la) ** 2)
    h = alt_m / 1000
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo), (n * (1 - e2) + h) * math.sin(la))


def look_angles(sat: Satrec, when: datetime, lat: float, lon: float, alt_m: float = 0) -> tuple[float, float, float] | None:
    """(azimut°, elevación°, distancia km) del satélite visto desde el observador."""
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    err, r, _ = sat.sgp4(jd, fr)
    if err:
        return None
    g = _gmst(jd + fr)
    x, y = r[0] * math.cos(g) + r[1] * math.sin(g), -r[0] * math.sin(g) + r[1] * math.cos(g)   # TEME -> ECEF
    ox, oy, oz = observer_ecef(lat, lon, alt_m)
    dx, dy, dz = x - ox, y - oy, r[2] - oz
    la, lo = math.radians(lat), math.radians(lon)
    e = -math.sin(lo) * dx + math.cos(lo) * dy
    n = -math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy + math.cos(la) * dz
    u = math.cos(la) * math.cos(lo) * dx + math.cos(la) * math.sin(lo) * dy + math.sin(la) * dz
    rng = math.sqrt(e * e + n * n + u * u)
    return (math.degrees(math.atan2(e, n)) % 360, math.degrees(math.asin(u / rng)), rng)


def passes(tle: dict, lat: float, lon: float, start: datetime, hours: float = 24, min_el: float = 10, alt_m: float = 0,
           step_s: int = 20) -> list[dict]:
    sat = Satrec.twoline2rv(tle["l1"], tle["l2"])
    out, cur, t = [], None, start
    end = start + timedelta(hours=hours)
    while t <= end:
        la = look_angles(sat, t, lat, lon, alt_m)
        if la and la[1] > 0:
            if cur is None:
                cur = {"aos": t, "track": []}
            cur["track"].append((t, la[0], la[1], la[2]))
        elif cur is not None:
            out.append(_finish(cur, tle, min_el))
            cur = None
        t += timedelta(seconds=step_s)
    if cur is not None:
        out.append(_finish(cur, tle, min_el))
    return [p for p in out if p and p["max_el"] >= min_el]


def _finish(cur: dict, tle: dict, min_el: float) -> dict | None:
    tr = cur["track"]
    best = max(tr, key=lambda p: p[2])
    return {"satelite": tle["name"], "norad": tle["norad"], "aos": tr[0][0].isoformat(), "aos_az": round(tr[0][1]),
            "max_el": round(best[2]), "max_el_t": best[0].isoformat(), "max_el_az": round(best[1]),
            "los": tr[-1][0].isoformat(), "los_az": round(tr[-1][1]), "duracion_s": int((tr[-1][0] - tr[0][0]).total_seconds()),
            "distancia_min_km": round(min(p[3] for p in tr)),
            "traza": [[round(p[1]), round(p[2], 1)] for p in tr[:: max(1, len(tr) // 40)]]}


def catalog(tles: list[dict]) -> list[dict]:
    return [{"norad": t["norad"], "nombre": t["name"], **({"info": ISS_INFO} if t["norad"] == 25544 else {})} for t in tles]
