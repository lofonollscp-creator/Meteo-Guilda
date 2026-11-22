"""Control de calidad de observaciones APRS."""
from __future__ import annotations

import math
import statistics
from typing import Iterable

from .aprs import WeatherObs

LIMITS = {
    "temp_c": (-60, 55),
    "wind_ms": (0, 80),
    "gust_ms": (0, 100),
    "humidity": (1, 100),
    "pressure_hpa": (850, 1090),
}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def range_ok(o: WeatherObs) -> bool:
    """False si algún valor está fuera de rango físico."""
    for k, (lo, hi) in LIMITS.items():
        v = getattr(o, k)
        if v is not None and not lo <= v <= hi:
            return False
    if o.gust_ms is not None and o.wind_ms is not None and o.gust_ms < o.wind_ms - 0.5:
        return False
    return True


def spatial_outlier(o: WeatherObs, others: Iterable[WeatherObs],
                    radius_km: float = 25, max_dev_c: float = 8, min_neighbors: int = 3) -> bool:
    """True si la temperatura difiere mucho de la mediana de vecinas cercanas."""
    if o.temp_c is None or o.lat is None:
        return False
    temps = [x.temp_c for x in others
             if x.callsign != o.callsign and x.temp_c is not None and x.lat is not None
             and haversine_km(o.lat, o.lon, x.lat, x.lon) <= radius_km]
    if len(temps) < min_neighbors:
        return False
    return abs(o.temp_c - statistics.median(temps)) > max_dev_c


def quality_flags(obs: list[WeatherObs]) -> dict[str, str]:
    """callsign -> 'ok' | 'range' | 'outlier'."""
    res = {}
    valid = [o for o in obs if range_ok(o)]
    for o in obs:
        if not range_ok(o):
            res[o.callsign] = "range"
        elif spatial_outlier(o, valid):
            res[o.callsign] = "outlier"
        else:
            res[o.callsign] = "ok"
    return res
