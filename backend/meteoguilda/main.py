"""API de Meteo Guilda. Arranque: uvicorn meteoguilda.main:app"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from . import ai, openmeteo, qc, store
from .qc import haversine_km

CACHE_TTL = 3 * 3600
_cache: dict[tuple, tuple[float, dict]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient()
    yield
    await app.state.http.aclose()


app = FastAPI(title="Meteo Guilda", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/stations")
def stations(south: float = -90, west: float = -180, north: float = 90, east: float = 180,
             max_age_min: int = 120):
    con = store.connect()
    obs = store.latest(con, max_age_min * 60, (south, west, north, east))
    flags = qc.quality_flags(obs)
    return [{**asdict(o), "quality": flags[o.callsign]} for o in obs]


def _nearby(lat: float, lon: float, radius_km: float = 40, limit: int = 6) -> list[dict]:
    obs = store.latest(store.connect(), 3 * 3600)
    flags = qc.quality_flags(obs)
    near = sorted(((haversine_km(lat, lon, o.lat, o.lon), o) for o in obs
                   if flags[o.callsign] == "ok"), key=lambda x: x[0])
    out = []
    for d, o in near[:limit]:
        if d > radius_km:
            break
        out.append({"id": o.callsign, "dist_km": round(d), "temp_c": o.temp_c,
                    "viento_kmh": round(o.wind_ms * 3.6) if o.wind_ms is not None else None,
                    "racha_kmh": round(o.gust_ms * 3.6) if o.gust_ms is not None else None,
                    "humedad_pct": o.humidity, "presion_hpa": o.pressure_hpa})
    return out


@app.get("/api/forecast")
async def forecast(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    key = (round(lat, 1), round(lon, 1))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    http: httpx.AsyncClient = app.state.http
    try:
        raw = await openmeteo.fetch(lat, lon, client=http)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e
    summary = openmeteo.summarize(raw)
    ctx = ai.build_context(summary, _nearby(lat, lon))
    res = await ai.generate(ctx, http)
    if res:
        fc, model, source = res[0], res[1], "ia"
    else:
        fc, model, source = ai.rules_forecast(ctx), None, "reglas"
    out = {"pronostico": fc.model_dump(), "origen": source, "modelo_ia": model,
           "datos": ctx, "generado": int(time.time())}
    _cache[key] = (time.time(), out)
    return out


_web = Path(__file__).resolve().parents[2] / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
