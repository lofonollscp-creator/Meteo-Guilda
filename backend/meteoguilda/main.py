"""API de Meteo Guilda. Arranque: uvicorn meteoguilda.main:app"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles

from . import ai, openmeteo, qc, store
from .places import BY_SLUG, PLACES, Place
from .qc import haversine_km

log = logging.getLogger("api")
SUMMARY_TTL = 3600          # modelos: 1 h
AI_TTL = 3 * 3600           # texto IA: 3 h
CUSTOM_LIMIT = (20, 3600)   # puntos libres: 20 por hora y por IP

_summaries: dict[tuple, tuple[float, dict]] = {}
_forecasts: dict[tuple, tuple[float, dict]] = {}
_hits: dict[str, deque] = defaultdict(deque)
_locks: dict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)


def _key(lat: float, lon: float) -> tuple:
    return (round(lat, 2), round(lon, 2))


async def get_summary(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = _key(lat, lon)
    async with _locks[("s", k)]:
        hit = _summaries.get(k)
        if hit and time.time() - hit[0] < SUMMARY_TTL:
            return hit[1]
        summary = openmeteo.summarize(await openmeteo.fetch(lat, lon, client=http))
        _summaries[k] = (time.time(), summary)
        return summary


def nearby_stations(lat: float, lon: float, radius_km: float = 40, limit: int = 6) -> list[dict]:
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


async def build_forecast(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = _key(lat, lon)
    async with _locks[("f", k)]:
        hit = _forecasts.get(k)
        if hit and time.time() - hit[0] < AI_TTL:
            return hit[1]
        summary = await get_summary(http, lat, lon)
        ctx = ai.build_context(summary, nearby_stations(lat, lon))
        res = await ai.generate(ctx, http)
        fc, model, origen = (res[0], res[1], "ia") if res else (ai.rules_forecast(ctx), None, "reglas")
        out = {"pronostico": fc.model_dump(), "origen": origen, "modelo_ia": model,
               "datos": ctx, "generado": int(time.time())}
        _forecasts[k] = (time.time(), out)
        return out


async def pregen_loop(http: httpx.AsyncClient):
    """Regenera los pronósticos de los lugares foco cada 3 h (cuida las cuotas gratuitas)."""
    while True:
        for p in PLACES:
            try:
                _forecasts.pop(_key(p.lat, p.lon), None)
                await build_forecast(http, p.lat, p.lon)
            except Exception as e:  # noqa: BLE001
                log.warning("pregen %s: %s", p.slug, e)
            await asyncio.sleep(20)
        await asyncio.sleep(AI_TTL - 20 * len(PLACES))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30)
    task = asyncio.create_task(pregen_loop(app.state.http)) if os.environ.get("MG_PREGEN") == "1" else None
    yield
    if task:
        task.cancel()
    await app.state.http.aclose()


app = FastAPI(title="Meteo Guilda", lifespan=lifespan, docs_url=None, redoc_url=None)


def _place_dict(p: Place) -> dict:
    return asdict(p)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/places")
async def places(request: Request):
    """Lugares foco con una instantánea (solo modelos, sin IA) para el listado."""
    http: httpx.AsyncClient = request.app.state.http

    async def one(p: Place):
        d = _place_dict(p)
        try:
            s = await get_summary(http, p.lat, p.lon)
            steps = s["steps"]
            first = steps[0]
            day = steps[:8]
            d["now"] = {
                "temp_c": first.get("temperature_2m", {}).get("mean"),
                "gust_kmh": round(max((x.get("wind_gusts_10m", {}).get("max", 0) for x in day)) * 3.6),
                "rain_24h_mm": round(sum(x.get("precipitation", {}).get("mean", 0) for x in day), 1),
                "confidence": first["confidence"],
                "spark": [x.get("temperature_2m", {}).get("mean") for x in steps[:16]],
            }
        except Exception as e:  # noqa: BLE001
            log.warning("places %s: %s", p.slug, e)
            d["now"] = None
        return d

    return await asyncio.gather(*(one(p) for p in PLACES))


@app.get("/api/stations")
def stations(south: float = -90, west: float = -180, north: float = 90, east: float = 180,
             max_age_min: int = 120):
    obs = store.latest(store.connect(), max_age_min * 60, (south, west, north, east))
    flags = qc.quality_flags(obs)
    return [{**asdict(o), "quality": flags[o.callsign]} for o in obs]


@app.get("/api/forecast")
async def forecast(request: Request, place: str | None = None,
                   lat: float | None = Query(None, ge=-90, le=90),
                   lon: float | None = Query(None, ge=-180, le=180)):
    if place:
        p = BY_SLUG.get(place)
        if not p:
            raise HTTPException(404, "Lugar desconocido")
        lat, lon = p.lat, p.lon
    elif lat is None or lon is None:
        raise HTTPException(422, "Indica place o lat+lon")
    else:  # punto libre: limitar por IP (protege cuotas de Open-Meteo y OpenRouter)
        ip = (request.headers.get("x-forwarded-for") or request.client.host).split(",")[0].strip()
        q, now = _hits[ip], time.time()
        while q and now - q[0] > CUSTOM_LIMIT[1]:
            q.popleft()
        if _forecasts.get(_key(lat, lon)) is None:
            if len(q) >= CUSTOM_LIMIT[0]:
                raise HTTPException(429, "Demasiadas consultas de puntos nuevos; inténtalo más tarde")
            q.append(now)
    try:
        out = await build_forecast(request.app.state.http, lat, lon)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e
    return {**out, "lugar": _place_dict(p) if place else {"name": "Punto del mapa", "lat": lat, "lon": lon}}


_web = Path(__file__).resolve().parents[2] / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
