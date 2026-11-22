"""API de Meteo Guilda. Arranque: uvicorn meteoguilda.main:app"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, db, geocode, openmeteo, qc, radio, routing, store
from .places import BY_SLUG, PLACES, Place

log = logging.getLogger("api")
SUMMARY_TTL, AI_TTL = 3600, 3 * 3600
LIMITS = {"point": (60, 3600), "route": (30, 3600), "search": (120, 600), "ai_route": (10, 3600)}

_summaries: dict[tuple, tuple[float, dict]] = {}
_forecasts: dict[tuple, tuple[float, dict]] = {}
_hits: dict[tuple, deque] = defaultdict(deque)
_locks: dict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)


def _key(lat: float, lon: float) -> tuple:
    return (round(lat, 2), round(lon, 2))


def limit(request: Request, bucket: str) -> None:
    """Límite por IP y ventana (protege las cuotas de Open-Meteo y OpenRouter)."""
    n, window = LIMITS[bucket]
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    q, now = _hits[(bucket, ip)], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= n:
        raise HTTPException(429, "Demasiadas consultas; inténtalo más tarde")
    q.append(now)


async def get_summary(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = _key(lat, lon)
    async with _locks[("s", k)]:
        hit = _summaries.get(k)
        if hit and time.time() - hit[0] < SUMMARY_TTL:
            return hit[1]
        summary = openmeteo.summarize(await openmeteo.fetch(lat, lon, client=http))
        _summaries[k] = (time.time(), summary)
        if len(_summaries) > 500:
            _summaries.pop(next(iter(_summaries)))
        return summary


def _station_dict(d: float, o) -> dict:
    return {"id": o.callsign, "dist_km": round(d), "lat": o.lat, "lon": o.lon, "temp_c": o.temp_c,
            "viento_kmh": round(o.wind_ms * 3.6) if o.wind_ms is not None else None,
            "racha_kmh": round(o.gust_ms * 3.6) if o.gust_ms is not None else None,
            "humedad_pct": o.humidity, "presion_hpa": o.pressure_hpa}


def _nearby_sync(lat: float, lon: float, radius_km: float, limit_n: int) -> list[dict]:
    near = store.nearby(lat, lon, radius_km)
    flags = qc.quality_flags([o for _, o in near])
    return [_station_dict(d, o) for d, o in near if flags[o.callsign] == "ok"][:limit_n]


async def nearby_stations(lat: float, lon: float, radius_km: float = 40, limit_n: int = 6) -> list[dict]:
    try:
        return await asyncio.to_thread(_nearby_sync, lat, lon, radius_km, limit_n)
    except Exception as e:  # noqa: BLE001  (sin BD, el pronóstico sigue funcionando)
        log.warning("estaciones no disponibles: %s", e)
        return []


async def build_forecast(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    k = _key(lat, lon)
    async with _locks[("f", k)]:
        hit = _forecasts.get(k)
        if hit and time.time() - hit[0] < AI_TTL:
            return hit[1]
        summary = await get_summary(http, lat, lon)
        ctx = ai.build_context(summary, await nearby_stations(lat, lon))
        res = await ai.generate(ctx, http)
        fc, model, origen = (res[0], res[1], "ia") if res else (ai.rules_forecast(ctx), None, "reglas")
        out = {"pronostico": fc.model_dump(), "origen": origen, "modelo_ia": model, "datos": ctx,
               "generado": int(time.time())}
        _forecasts[k] = (time.time(), out)
        if len(_forecasts) > 500:
            _forecasts.pop(next(iter(_forecasts)))
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
    app.state.http = httpx.AsyncClient(timeout=30, headers={"User-Agent": "MeteoGuilda/0.2"})
    try:
        await asyncio.to_thread(db.init_schema)
    except Exception as e:  # noqa: BLE001
        log.warning("PostGIS no disponible (%s); estaciones y repetidores desactivados", e)
    task = asyncio.create_task(pregen_loop(app.state.http)) if os.environ.get("MG_PREGEN") == "1" else None
    yield
    if task:
        task.cancel()
    await app.state.http.aclose()
    db.close()


app = FastAPI(title="Meteo Guilda", lifespan=lifespan, docs_url=None, redoc_url=None)
H = lambda r: r.app.state.http  # noqa: E731


@app.get("/api/health")
async def health():
    return {"ok": True, "db": await asyncio.to_thread(db.available)}


# ---------- lugares, búsqueda, instantáneas ----------
async def snapshot(http: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    try:
        s = await get_summary(http, lat, lon)
        steps = s["steps"]
        day = steps[:8]
        return {"temp_c": steps[0].get("temperature_2m", {}).get("mean"),
                "gust_kmh": round(max(x.get("wind_gusts_10m", {}).get("max", 0) for x in day) * 3.6),
                "rain_24h_mm": round(sum(x.get("precipitation", {}).get("mean", 0) for x in day), 1),
                "confidence": steps[0]["confidence"], "elevation_m": s["elevation_m"],
                "spark": [x.get("temperature_2m", {}).get("mean") for x in steps[:16]]}
    except Exception as e:  # noqa: BLE001
        log.warning("snapshot %s,%s: %s", lat, lon, e)
        return None


@app.get("/api/places")
async def places(request: Request):
    http = H(request)
    snaps = await asyncio.gather(*(snapshot(http, p.lat, p.lon) for p in PLACES))
    return [{**asdict(p), "now": s} for p, s in zip(PLACES, snaps)]


@app.get("/api/snapshot")
async def snapshots(request: Request, pts: str = Query(max_length=600)):
    """Instantánea de hasta 15 puntos 'lat,lon;lat,lon' (para favoritos)."""
    try:
        points = [tuple(map(float, p.split(","))) for p in pts.split(";") if p][:15]
    except ValueError as e:
        raise HTTPException(422, "Formato: lat,lon;lat,lon") from e
    limit(request, "point")
    res = await asyncio.gather(*(snapshot(H(request), la, lo) for la, lo in points))
    return [{"lat": la, "lon": lo, "now": s} for (la, lo), s in zip(points, res)]


@app.get("/api/search")
async def search(request: Request, q: str = Query(min_length=2, max_length=80)):
    limit(request, "search")
    try:
        return await geocode.search(H(request), q)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Buscador no disponible: {e}") from e


# ---------- previsión ----------
def _point(place: str | None, lat: float | None, lon: float | None) -> tuple[float, float, Place | None]:
    if place:
        p = BY_SLUG.get(place)
        if not p:
            raise HTTPException(404, "Lugar desconocido")
        return p.lat, p.lon, p
    if lat is None or lon is None:
        raise HTTPException(422, "Indica place o lat+lon")
    return lat, lon, None


@app.get("/api/forecast")
async def forecast(request: Request, place: str | None = None, lat: float | None = Query(None, ge=-90, le=90),
                   lon: float | None = Query(None, ge=-180, le=180), name: str | None = Query(None, max_length=80)):
    la, lo, p = _point(place, lat, lon)
    if not p and _forecasts.get(_key(la, lo)) is None:
        limit(request, "point")
    try:
        out = await build_forecast(H(request), la, lo)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e
    lugar = asdict(p) if p else {"name": name or "Punto del mapa", "lat": la, "lon": lo, "kind": "libre"}
    return {**out, "lugar": lugar}


@app.get("/api/raw")
async def raw(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Datos de modelos y balizas SIN texto de IA (para clientes MCP y modelos externos)."""
    limit(request, "point")
    ctx = ai.build_context(await get_summary(H(request), lat, lon), await nearby_stations(lat, lon))
    return {"datos": ctx, "unidades": {"viento": "km/h", "temp": "°C", "lluvia": "mm/3h", "nieve": "cm/3h",
                                         "isocero": "m", "presion": "hPa", "cape": "J/kg"},
            "nota": "Media entre modelos ECMWF IFS, DWD ICON, NOAA GFS y Météo-France; 'confianza' según su dispersión."}


@app.get("/api/stations")
async def stations(south: float = -90, west: float = -180, north: float = 90, east: float = 180, max_age_min: int = 120):
    try:
        obs = await asyncio.to_thread(store.latest, max_age_min * 60, (south, west, north, east))
    except Exception:  # noqa: BLE001
        return []
    flags = qc.quality_flags(obs)
    return [{**asdict(o), "quality": flags[o.callsign]} for o in obs]


@app.get("/api/observations")
async def observations(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                       km: float = Query(40, gt=0, le=150)):
    return await nearby_stations(lat, lon, km, 15)


# ---------- rutas ----------
class Pt(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteReq(BaseModel):
    waypoints: list[Pt] = Field(default_factory=list, max_length=10)
    gpx: str | None = Field(None, max_length=3_000_000)
    mode: Literal["foot", "car"] = "foot"
    depart: datetime | None = None
    ia: bool = False


@app.post("/api/route")
async def route(request: Request, req: RouteReq):
    limit(request, "route")
    dep = req.depart
    if dep is not None and dep.tzinfo is None:
        dep = dep.replace(tzinfo=timezone.utc)
    try:
        res = await routing.plan(H(request), waypoints=[(p.lat, p.lon) for p in req.waypoints], gpx=req.gpx,
                                 mode=req.mode, depart=dep)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(502, f"No se pudo calcular la ruta: {e}") from e
    coords = [(la, lo) for lo, la in res["geometria"]]
    try:
        near = await asyncio.to_thread(store.near_line, coords, 5)
        res["balizas"] = [_station_dict(d, o) for d, o in near][:12]
        res["repetidores"] = [{k: v for k, v in r.items()} for r in await asyncio.to_thread(radio.repeaters_along, coords)]
    except Exception as e:  # noqa: BLE001
        log.warning("balizas/repetidores en ruta: %s", e)
        res["balizas"], res["repetidores"] = [], []
    res["lectura"] = None
    if req.ia:
        limit(request, "ai_route")
        ctx = {"modo": res["modo"], "distancia_km": res["distancia_km"], "duracion_min": res["duracion_min"],
               "ascenso_m": res["ascenso_m"], "descenso_m": res["descenso_m"], "salida": res["salida"],
               "nivel_global": res["nivel_global"], "nivel_texto": res["nivel_texto"],
               "muestras": [{k: m.get(k) for k in ("d_km", "ele", "t", "nivel", "motivos", "meteo")} for m in res["muestras"]],
               "ventanas": res["ventanas"]}
        out = await ai.generate(ctx, H(request), system=ai.ROUTE_SYSTEM, trim=False)
        if out:
            res["lectura"] = {**out[0].model_dump(), "modelo_ia": out[1]}
    return res


# ---------- radio ----------
@app.get("/api/propagation")
async def propagation(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    limit(request, "point")
    try:
        return {**await radio.propagation(H(request), lat, lon), "locator": radio.to_locator(lat, lon)}
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e


@app.get("/api/solar")
async def solar(request: Request):
    return await radio.solar(H(request))


@app.get("/api/locator")
def locator(lat: float | None = None, lon: float | None = None, loc: str | None = None):
    try:
        if loc:
            la, lo = radio.from_locator(loc)
            return {"locator": loc.upper(), "lat": round(la, 5), "lon": round(lo, 5)}
        if lat is None or lon is None:
            raise HTTPException(422, "Indica lat+lon o loc")
        return {"locator": radio.to_locator(lat, lon), "lat": lat, "lon": lon}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _bbox(s: str | None):
    if not s:
        return None
    try:
        a = tuple(map(float, s.split(",")))
        assert len(a) == 4
        return a  # sur,oeste,norte,este
    except (ValueError, AssertionError) as e:
        raise HTTPException(422, "bbox: sur,oeste,norte,este") from e


@app.get("/api/repeaters")
async def repeaters(bbox: str | None = None, band: str | None = None, coverage: bool = False):
    try:
        return await asyncio.to_thread(radio.list_repeaters, _bbox(bbox), band, coverage)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e


@app.get("/api/repeaters/near")
async def repeaters_near(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), km: float = Query(60, le=300)):
    try:
        return await asyncio.to_thread(radio.repeaters_near, lat, lon, km)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e


def _admin(token: str | None):
    expected = os.environ.get("MG_ADMIN_TOKEN")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "No autorizado")


class RepIn(BaseModel):
    name: str
    callsign: str | None = None
    band: str | None = None
    freq_mhz: float
    shift_khz: int | None = None
    tone_hz: float | None = None
    mode: str = "FM"
    height_m: float = 10
    range_km: float = Field(40, le=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source: str | None = None


@app.post("/api/admin/repeaters")
async def add_repeater(rep: RepIn, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    return {"id": await asyncio.to_thread(radio.upsert_repeater, rep.model_dump())}


@app.post("/api/admin/repeaters/{rep_id}/coverage")
async def repeater_coverage(request: Request, rep_id: int, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    try:
        return await radio.compute_coverage(H(request), rep_id)
    except KeyError as e:
        raise HTTPException(404, "Repetidor no encontrado") from e


_web = Path(__file__).resolve().parents[2] / "web"
if _web.is_dir():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
