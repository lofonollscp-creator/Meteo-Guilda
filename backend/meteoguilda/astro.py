"""Sol y Luna: orto/ocaso, crepúsculos, fase lunar (algoritmos simples, precisión de minutos)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

SYNODIC = 29.530588853
NEW_MOON_JD = 2451550.1          # luna nueva de referencia (6 ene 2000)
PHASES = [(1.0, "Luna nueva"), (6.4, "Creciente"), (8.4, "Cuarto creciente"), (13.8, "Gibosa creciente"), (15.8, "Luna llena"),
          (21.1, "Gibosa menguante"), (23.1, "Cuarto menguante"), (28.5, "Menguante"), (99, "Luna nueva")]


def jd(dt: datetime) -> float:
    return dt.timestamp() / 86400 + 2440587.5


def sun_event(lat: float, lon: float, day: date, alt_deg: float = -0.833) -> tuple[datetime | None, datetime | None, datetime | None]:
    """(salida, tránsito, puesta) UTC del Sol para el día civil dado, con altura alt_deg sobre el horizonte."""
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    n = round(jd(noon) - 2451545.0)          # día juliano (mediodía UTC) desde J2000
    js = n - lon / 360          # lon al este positiva: al oeste el mediodía solar llega más tarde
    m = (357.5291 + 0.98560028 * js) % 360
    mr = math.radians(m)
    c = 1.9148 * math.sin(mr) + 0.02 * math.sin(2 * mr) + 0.0003 * math.sin(3 * mr)
    lam = math.radians((m + c + 180 + 102.9372) % 360)
    transit = 2451545.0 + js + 0.0053 * math.sin(mr) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    cosw = (math.sin(math.radians(alt_deg)) - math.sin(math.radians(lat)) * math.sin(dec)) / (math.cos(math.radians(lat)) * math.cos(dec))
    to_dt = lambda j: datetime.fromtimestamp((j - 2440587.5) * 86400, tz=timezone.utc)  # noqa: E731
    if abs(cosw) >= 1:
        return None, to_dt(transit), None
    w = math.degrees(math.acos(cosw)) / 360
    return to_dt(transit - w), to_dt(transit), to_dt(transit + w)


def moon(dt: datetime) -> dict:
    age = (jd(dt) - NEW_MOON_JD) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2
    name = next(n for lim, n in PHASES if age <= lim)
    nxt_new = dt + timedelta(days=SYNODIC - age)
    nxt_full = dt + timedelta(days=(SYNODIC / 2 - age) % SYNODIC)
    return {"edad_dias": round(age, 1), "iluminacion_pct": round(illum * 100), "fase": name, "creciente": age < SYNODIC / 2,
            "proxima_llena": nxt_full.isoformat(), "proxima_nueva": nxt_new.isoformat()}


def sun_moon(lat: float, lon: float, dt: datetime | None = None) -> dict:
    dt = dt or datetime.now(timezone.utc)
    d = dt.date()
    rise, transit, sset = sun_event(lat, lon, d)
    cs, _, ce = sun_event(lat, lon, d, -6)
    iso = lambda x: x.isoformat() if x else None  # noqa: E731
    return {"fecha": d.isoformat(), "orto": iso(rise), "mediodia_solar": iso(transit), "ocaso": iso(sset),
            "crepusculo_civil_inicio": iso(cs), "crepusculo_civil_fin": iso(ce),
            "horas_de_luz": round((sset - rise).total_seconds() / 3600, 2) if rise and sset else (24.0 if transit and not rise and ce else 0.0),
            "luna": moon(dt), "horas": "UTC"}
