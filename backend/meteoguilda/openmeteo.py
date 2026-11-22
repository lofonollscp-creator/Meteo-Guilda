"""Cliente Open-Meteo multi-modelo + resumen determinista (acuerdo entre modelos)."""
from __future__ import annotations

import statistics
from datetime import datetime

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless", "meteofrance_seamless"]
VARS = ["temperature_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m", "cloud_cover"]
# Dispersión (máx-mín entre modelos) a partir de la cual la confianza baja
SPREAD = {"temperature_2m": (2.0, 5.0), "precipitation": (1.0, 3.0),
          "wind_speed_10m": (2.0, 5.0), "wind_gusts_10m": (3.0, 8.0), "cloud_cover": (25, 50)}


async def fetch(lat: float, lon: float, days: int = 3, client: httpx.AsyncClient | None = None) -> dict:
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARS + ["freezing_level_height"]),
              "models": ",".join(MODELS), "wind_speed_unit": "ms", "forecast_days": days,
              "timezone": "auto"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(URL, params=params)
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            await client.aclose()


def _confidence(var: str, spread: float, n_models: int) -> str:
    if n_models < 2:
        return "baja"
    lo, hi = SPREAD.get(var, (2, 5))
    return "alta" if spread <= lo else "media" if spread <= hi else "baja"


def summarize(data: dict, step_h: int = 3, max_steps: int = 24) -> dict:
    """Agrega por tramos de `step_h` horas: media entre modelos, rango y confianza."""
    h = data["hourly"]
    times = h["time"]
    series: dict[str, list[list]] = {}
    for var in VARS + ["freezing_level_height"]:
        cols = [v for k, v in h.items() if k == var or k.startswith(var + "_")]
        series[var] = cols
    steps = []
    for i in range(0, min(len(times), step_h * max_steps), step_h):
        row = {"time": times[i]}
        confs = []
        for var, cols in series.items():
            vals_per_model = []
            for c in cols:
                chunk = [x for x in c[i:i + step_h] if x is not None]
                if chunk:
                    vals_per_model.append(chunk)
            if not vals_per_model:
                continue
            if var == "precipitation":
                per_model = [sum(c) for c in vals_per_model]
            elif var == "wind_gusts_10m":
                per_model = [max(c) for c in vals_per_model]
            else:
                per_model = [statistics.fmean(c) for c in vals_per_model]
            mean = statistics.fmean(per_model)
            spread = max(per_model) - min(per_model)
            row[var] = {"mean": round(mean, 1), "min": round(min(per_model), 1),
                        "max": round(max(per_model), 1)}
            if var != "freezing_level_height":
                confs.append(_confidence(var, spread, len(per_model)))
        row["confidence"] = ("baja" if "baja" in confs else "media" if "media" in confs else "alta"
                             ) if confs else "baja"
        steps.append(row)
    return {"lat": data.get("latitude"), "lon": data.get("longitude"),
            "elevation_m": data.get("elevation"), "models": MODELS, "steps": steps}


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s)
