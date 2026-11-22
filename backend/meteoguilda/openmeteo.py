"""Cliente Open-Meteo multi-modelo: resúmenes por tramos, consenso horario, perfil de elevación."""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
ELEV_URL = "https://api.open-meteo.com/v1/elevation"
MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless", "meteofrance_seamless"]
VARS = ["temperature_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
        "cloud_cover", "relative_humidity_2m", "surface_pressure", "snowfall", "cape", "freezing_level_height"]
# Dispersión (máx-mín entre modelos) a partir de la cual baja la confianza: (alta, media)
SPREAD = {"temperature_2m": (2.0, 5.0), "precipitation": (1.0, 3.0), "wind_speed_10m": (2.0, 5.0),
          "wind_gusts_10m": (3.0, 8.0), "cloud_cover": (25, 50), "surface_pressure": (2, 5)}
CONF_VARS = tuple(SPREAD)
SUM_VARS = {"precipitation", "snowfall"}
MAX_VARS = {"wind_gusts_10m", "cape"}


def _params(lat, lon, days: int, models: list[str] = MODELS, extra: str = "") -> dict:
    return {"latitude": lat, "longitude": lon, "hourly": ",".join(VARS) + extra,
            "models": ",".join(models), "wind_speed_unit": "ms", "forecast_days": days, "timezone": "auto"}


async def fetch(lat: float, lon: float, days: int = 5, client: httpx.AsyncClient | None = None) -> dict:
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(URL, params=_params(lat, lon, days))
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            await client.aclose()


async def fetch_many(points: list[tuple[float, float]], days: int = 3,
                     client: httpx.AsyncClient | None = None) -> list[dict]:
    """Una sola petición para varios puntos [(lat, lon), ...]. Devuelve una lista (una por punto)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=40)
    try:
        r = await client.get(URL, params=_params(",".join(f"{p[0]:.4f}" for p in points),
                                                 ",".join(f"{p[1]:.4f}" for p in points), days))
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) else [d]
    finally:
        if own:
            await client.aclose()


async def elevations(points: list[tuple[float, float]], client: httpx.AsyncClient) -> list[float]:
    """Elevación DEM (m) de hasta N puntos (lotes de 100)."""
    out: list[float] = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        r = await client.get(ELEV_URL, params={"latitude": ",".join(f"{p[0]:.5f}" for p in chunk),
                                               "longitude": ",".join(f"{p[1]:.5f}" for p in chunk)})
        r.raise_for_status()
        out += r.json()["elevation"]
    return out


# ---------- utilidades de series ----------
def _start_index(data: dict, now: datetime | None = None) -> int:
    """Índice de la primera hora >= ahora (hora local del punto). La API devuelve desde las 00:00."""
    times = data["hourly"]["time"]
    now = now or datetime.now(timezone.utc)
    local = (now + timedelta(seconds=data.get("utc_offset_seconds", 0))).replace(tzinfo=None)
    cur = local.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    for i, t in enumerate(times):
        if t >= cur:
            return i
    return 0


def _cols(h: dict, var: str) -> list[list]:
    return [v for k, v in h.items() if k == var or k.startswith(var + "_") and k[len(var) + 1:] in _MODEL_SUFFIX]


_MODEL_SUFFIX = set(MODELS) | {"best_match"}


def circ_mean(deg: list[float]) -> float:
    s = sum(math.sin(math.radians(d)) for d in deg)
    c = sum(math.cos(math.radians(d)) for d in deg)
    return round(math.degrees(math.atan2(s, c)) % 360)


def _confidence(var: str, spread: float, n_models: int) -> str:
    if n_models < 2:
        return "baja"
    lo, hi = SPREAD.get(var, (2, 5))
    return "alta" if spread <= lo else "media" if spread <= hi else "baja"


def hourly_consensus(data: dict, now: datetime | None = None) -> dict:
    """Media entre modelos hora a hora desde ahora: {'time': [...], var: [valor|None]}."""
    k0 = _start_index(data, now)
    h = data["hourly"]
    out: dict = {"time": h["time"][k0:], "utc_offset": data.get("utc_offset_seconds", 0),
                 "elevation": data.get("elevation")}
    for var in VARS:
        cols = [c[k0:] for c in _cols(h, var)]
        vals = []
        for i in range(len(out["time"])):
            xs = [c[i] for c in cols if i < len(c) and c[i] is not None]
            if not xs:
                vals.append(None)
            elif var == "wind_direction_10m":
                vals.append(circ_mean(xs))
            else:
                vals.append(statistics.fmean(xs))
        out[var] = vals
    return out


def summarize(data: dict, step_h: int = 3, max_steps: int = 40, now: datetime | None = None) -> dict:
    """Tramos de `step_h` h desde ahora: media entre modelos, rango y confianza."""
    k0 = _start_index(data, now)
    h = {k: v[k0:] for k, v in data["hourly"].items()}
    times = h["time"]
    series = {var: _cols(h, var) for var in VARS}
    steps = []
    for i in range(0, min(len(times), step_h * max_steps), step_h):
        row: dict = {"time": times[i]}
        confs = []
        for var, cols in series.items():
            per_model = []
            for c in cols:
                chunk = [x for x in c[i:i + step_h] if x is not None]
                if chunk:
                    per_model.append(chunk)
            if not per_model:
                continue
            if var == "wind_direction_10m":
                row[var] = {"mean": circ_mean([statistics.fmean(c) for c in per_model])}
                continue
            if var in SUM_VARS:
                vals = [sum(c) for c in per_model]
            elif var in MAX_VARS:
                vals = [max(c) for c in per_model]
            else:
                vals = [statistics.fmean(c) for c in per_model]
            spread = max(vals) - min(vals)
            row[var] = {"mean": round(statistics.fmean(vals), 1), "min": round(min(vals), 1),
                        "max": round(max(vals), 1)}
            if var in CONF_VARS:
                confs.append(_confidence(var, spread, len(vals)))
        row["confidence"] = ("baja" if "baja" in confs else "media" if "media" in confs else "alta") if confs else "baja"
        steps.append(row)
    return {"lat": data.get("latitude"), "lon": data.get("longitude"), "elevation_m": data.get("elevation"),
            "models": MODELS, "steps": steps}


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)
