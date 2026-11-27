"""API de Meteo Guilda. Arranque: uvicorn meteoguilda.main:app"""
from __future__ import annotations

import asyncio
import hmac
import json
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
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ventanas as ventanas_mod
from . import avisos as avisos_mod
from . import meteocat
from . import verif
from . import push, reglas, memoria, configuracion, uso_ia, estado as estado_mod
from . import alertas, rutas_guardadas, qsl_servicios
from . import mar as mar_mod
from . import comparar as comparar_mod
from .cuota import cuota as cuota_om, hook_antes as _cuota_antes, hook_despues as _cuota_despues
from . import assistant_tools, account, activities, airq, auth, oauth, sources, ai, astro, db, geocode, logbook, openmeteo, portable, qc, radio, repeater_sources, routing, satellites, store
from .places import BY_SLUG, PLACES, Place

log = logging.getLogger("api")
# Open-Meteo cobra por modelo (8 modelos ≈ 8,8 de coste por consulta; límite gratuito 600/min y 10 000/día): caché de 2 h y recarga manual con enfriamiento
SUMMARY_TTL, AI_TTL, REFRESH_COOLDOWN = 2 * 3600, 3 * 3600, 300
LIMITS = {"reverse": (60, 3600), "assistant": (40, 3600), "ai": (60, 3600), "refresh": (20, 3600), "point": (60, 3600), "route": (30, 3600), "search": (120, 600), "ai_route": (10, 3600), "profile": (5, 3600),
          "coverage": (6, 3600), "sat": (60, 3600), "portable": (60, 3600)}

_summaries: dict[tuple, tuple[float, dict]] = {}
_forecasts: dict[tuple, tuple[float, dict]] = {}
_ai: dict[tuple, tuple[float, dict]] = {}
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


async def get_summary(http: httpx.AsyncClient, lat: float, lon: float, force: bool = False) -> dict:
    k = _key(lat, lon)
    async with _locks[("s", k)]:
        hit = _summaries.get(k)
        if hit and not force and time.time() - hit[0] < SUMMARY_TTL:
            return hit[1]
        try:
            raw = await openmeteo.fetch(lat, lon, client=http)
            slug = verif.lugar_de(lat, lon)
            summary = openmeteo.summarize(raw, pesos=await asyncio.to_thread(verif.pesos, slug) if slug else None)
            if slug:                  # lugares principales: guarda lo previsto para verificarlo luego (ver verif.py)
                await asyncio.to_thread(verif.registrar, raw, slug)
        except httpx.HTTPError:
            if hit:   # la fuente limita o falla: mejor datos de hace unas horas que un error
                log.warning("Open-Meteo no responde; se usan datos guardados de %s", k)
                return hit[1]
            raise
        _summaries[k] = (time.time(), summary)
        if len(_summaries) > 500:
            _summaries.pop(next(iter(_summaries)))
        return summary


def _station_dict(d: float, o) -> dict:
    return {"id": o.callsign, "fuente": "APRS", "dist_km": round(d), "lat": o.lat, "lon": o.lon, "temp_c": o.temp_c,
            "viento_kmh": round(o.wind_ms * 3.6) if o.wind_ms is not None else None,
            "racha_kmh": round(o.gust_ms * 3.6) if o.gust_ms is not None else None,
            "humedad_pct": o.humidity, "presion_hpa": o.pressure_hpa}


def _nearby_sync(lat: float, lon: float, radius_km: float, limit_n: int) -> list[dict]:
    near = store.nearby(lat, lon, radius_km)
    flags = qc.quality_flags([o for _, o in near])
    return [_station_dict(d, o) for d, o in near if flags[o.callsign] == "ok"][:limit_n]


async def nearby_stations(lat: float, lon: float, radius_km: float = 40, limit_n: int = 6,
                          http: httpx.AsyncClient | None = None) -> list[dict]:
    """Observaciones reales cercanas: balizas APRS (con control de calidad) + METAR + XEMA (Cataluña), de más a menos cerca."""
    async def aprs() -> list[dict]:
        try:
            return await asyncio.to_thread(_nearby_sync, lat, lon, radius_km, limit_n)
        except Exception as e:  # noqa: BLE001  (sin BD, el pronóstico sigue funcionando)
            log.warning("estaciones no disponibles: %s", e)
            return []
    a, extra = await asyncio.gather(aprs(), sources.nearby(http, lat, lon, radius_km) if http else asyncio.sleep(0, []))
    rows = sorted([*a, *extra], key=lambda o: o["dist_km"])
    return rows[:limit_n]


async def build_forecast(http: httpx.AsyncClient, lat: float, lon: float, force: bool = False) -> dict:
    """Datos de modelos + observaciones + resumen por reglas. NO espera a la IA (tarda segundos): va aparte en ai_forecast()."""
    k = _key(lat, lon)
    async with _locks[("f", k)]:
        hit = _forecasts.get(k)
        if hit and not force and time.time() - hit[0] < SUMMARY_TTL:
            return hit[1]
        summary = await get_summary(http, lat, lon, force)
        ctx = ai.build_context(summary, await nearby_stations(lat, lon, limit_n=12, http=http))
        out = {"pronostico": ai.rules_forecast(ctx).model_dump(), "origen": "reglas", "modelo_ia": None, "datos": ctx,
               "generado": int(time.time())}
        _forecasts[k] = (time.time(), out)
        if len(_forecasts) > 500:
            _forecasts.pop(next(iter(_forecasts)))
        return out


def _cfg_sig(cfg: ai.AIConfig) -> str:
    import hashlib
    return f"{cfg.provider}:{cfg.model}:{hashlib.sha256((cfg.api_key or '').encode()).hexdigest()[:8] if cfg.api_key else ''}"


async def ai_forecast(http: httpx.AsyncClient, lat: float, lon: float, cfg: ai.AIConfig, cached_only: bool = False) -> dict:
    """Lectura de IA del pronóstico (caché de 3 h por lugar y configuración; si falla, se recuerda 5 min y se devuelve el resumen por reglas)."""
    k = (_key(lat, lon), _cfg_sig(cfg))
    if cached_only:   # no genera nada (no gasta tokens): solo devuelve una lectura de IA ya hecha y vigente
        hit = _ai.get(k)
        return hit[1] if hit and hit[1].get("origen") == "ia" and time.time() - hit[0] < AI_TTL else {"origen": "pendiente"}
    async with _locks[("a", k)]:
        hit = _ai.get(k)
        if hit and time.time() - hit[0] < AI_TTL:
            return hit[1]
        base = await build_forecast(http, lat, lon)
        res = await ai.generate(base["datos"], http, cfg=cfg)
        out = ({"pronostico": res[0].model_dump(), "origen": "ia", "modelo_ia": res[1]} if res
               else {"pronostico": base["pronostico"], "origen": "reglas", "modelo_ia": None,
                     "aviso": f"{getattr(ai.generate, 'last_why', '') or 'La IA no ha podido responder ahora.'} Se muestra el resumen automático; inténtalo en unos minutos."})
        out["generado"] = int(time.time())
        _ai[k] = (time.time() if res else time.time() - AI_TTL + 300, out)
        if len(_ai) > 500:
            _ai.pop(next(iter(_ai)))
        return out


async def pregen_loop(http: httpx.AsyncClient):
    """Regenera los datos de los lugares foco cada hora (sin IA: esa se pide al abrir un lugar y se cachea 3 h)."""
    while True:
        for p in PLACES:
            try:
                await build_forecast(http, p.lat, p.lon, force=True)
            except Exception as e:  # noqa: BLE001
                log.warning("pregen %s: %s", p.slug, e)
            await asyncio.sleep(20)
        await verif.verificar(http)
        await asyncio.sleep(SUMMARY_TTL - 20 * len(PLACES))


async def repeaters_loop(http: httpx.AsyncClient):
    """Sincroniza repetidores (BrandMeister + HearHam) al arrancar si hace falta y cada 24 h."""
    while True:
        try:
            if await asyncio.to_thread(repeater_sources.needs_sync):
                log.info("repetidores: %s", await repeater_sources.sync(http))
        except Exception as e:  # noqa: BLE001
            log.warning("sincronización de repetidores: %s", e)
        await asyncio.sleep(24 * 3600)


_tropo_cache: dict[tuple, tuple[float, dict]] = {}


async def tropo_cacheada(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    """Propagación tropo con caché de 3 h por punto (~10 km): la usan las alertas sin gastar una consulta por cada una."""
    k = (round(lat, 1), round(lon, 1))
    hit = _tropo_cache.get(k)
    if hit and time.time() - hit[0] < 3 * 3600:
        return hit[1]
    out = await radio.propagation(http, lat, lon)
    if len(_tropo_cache) > 300:
        _tropo_cache.clear()
    _tropo_cache[k] = (time.time(), out)
    return out


def fuentes_alertas(http: httpx.AsyncClient) -> alertas.Fuentes:
    """Datos que necesitan las alertas (alertas.py), con las mismas cachés que la API."""
    async def pasos(lat, lon, norad, horas, min_el):
        return (await _sat_passes(http, lat, lon, horas, min_el, ",".join(map(str, norad))))[1]
    return alertas.Fuentes(resumen=lambda la, lo: get_summary(http, la, lo), mar=lambda la, lo: mar_mod.fetch(http, la, lo),
                           tropo=lambda la, lo: tropo_cacheada(http, la, lo), solar=lambda: radio.solar(http), pasos=pasos,
                           avisos=lambda: avisos_mod.todos(http))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=30, headers={"User-Agent": "MeteoGuilda/0.2"},
                                       event_hooks={"request": [_cuota_antes], "response": [_cuota_despues]})
    try:
        await asyncio.to_thread(db.init_schema)
    except Exception as e:  # noqa: BLE001
        log.warning("PostGIS no disponible (%s); estaciones y repetidores desactivados", e)
    app.state.fuentes_alertas = fuentes_alertas(app.state.http)
    for problema in configuracion.avisos():
        log.warning("configuración sospechosa: %s", problema)
    tasks = []
    if os.environ.get("MG_PREGEN") == "1":
        tasks.append(asyncio.create_task(pregen_loop(app.state.http)))
        tasks.append(asyncio.create_task(push.bucle(app.state.http, (lambda la, lo: get_summary(app.state.http, la, lo), lambda la, lo: mar_mod.fetch(app.state.http, la, lo)),
                                                    app.state.fuentes_alertas)))      # notificaciones push (un solo proceso: el que tiene MG_PREGEN)
    if os.environ.get("MG_REPEATERS_SYNC") == "1":
        tasks.append(asyncio.create_task(repeaters_loop(app.state.http)))
    yield
    for t in tasks:
        t.cancel()
    await app.state.http.aclose()
    db.close()


app = FastAPI(title="Meteo Guilda · Radio Guilda", version="0.3", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url="/api/openapi.json")
H = lambda r: r.app.state.http  # noqa: E731
app.include_router(oauth.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(push.router)
app.include_router(reglas.router)
app.include_router(memoria.router)
app.include_router(alertas.router)
app.include_router(rutas_guardadas.router)
app.include_router(qsl_servicios.router)


@app.get("/api/cuota")
def cuota_estado():
    """Uso de Open-Meteo (peticiones del último minuto, hora y día frente a su límite gratuito) y si hay un freno por 429."""
    return cuota_om.estado()


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
                   lon: float | None = Query(None, ge=-180, le=180), name: str | None = Query(None, max_length=80),
                   refresh: bool = False):
    """Datos y resumen por reglas, SIN esperar a la IA (la lectura de IA se pide aparte en /api/forecast/ai).
    `refresh=1` vuelve a consultar los modelos (como mucho una vez cada 5 min por lugar)."""
    la, lo, p = _point(place, lat, lon)
    hit = _forecasts.get(_key(la, lo))
    force = False
    if refresh and (not hit or time.time() - hit[0] > REFRESH_COOLDOWN):
        limit(request, "refresh")
        force = True
    elif not p and hit is None:
        limit(request, "point")
    try:
        out = await build_forecast(H(request), la, lo, force=force)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e
    lugar = asdict(p) if p else {"name": name or "Punto del mapa", "lat": la, "lon": lo, "kind": "libre"}
    return {**out, "lugar": lugar}


@app.get("/api/avisos")
async def avisos(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Avisos oficiales vigentes (AEMET/Meteoalerta) que afectan al punto, más los enlaces oficiales. Sin clave de AEMET solo salen los enlaces."""
    limit(request, "point")
    return avisos_mod.para_punto(await avisos_mod.todos(H(request)), lat, lon)


@app.get("/api/precision")
async def precision(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), dias: int = Query(30, ge=7, le=90)):
    """Qué modelo ha acertado más la temperatura (a 24 h vista) en el lugar principal más cercano (≤ 60 km), según las estaciones oficiales."""
    limit(request, "point")
    cerca = min(PLACES, key=lambda p: sources.haversine(lat, lon, p.lat, p.lon))
    if sources.haversine(lat, lon, cerca.lat, cerca.lon) > 60:
        return {"disponible": False, "motivo": "sin_lugar_cercano"}
    modelos = await asyncio.to_thread(verif.precision, cerca.slug, dias)
    return {"disponible": bool(modelos), "lugar": cerca.name, "dias": dias, "lead_h": 24, "minimo_n": verif.MIN_N, "modelos": modelos,
            "nota": "Error medio de la temperatura prevista a 24 h frente a la medida por estaciones oficiales (XEMA/METAR) a ≤ 25 km."}


@app.get("/api/oficial")
async def oficial(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Predicción oficial a 8 días del Meteocat (solo Cataluña; plan de 100 consultas/mes: caché de 18 h y topes propios, ver meteocat.py)."""
    limit(request, "point")
    return await meteocat.prediccion(H(request), lat, lon)


@app.get("/api/forecast/ai")
async def forecast_ai(request: Request, place: str | None = None, lat: float | None = Query(None, ge=-90, le=90),
                      lon: float | None = Query(None, ge=-180, le=180), solo_cache: bool = False):
    """Lectura de IA con el proveedor y el modelo de los ajustes de la persona. SOLO se genera si se pide (consume tokens): con `solo_cache=1`
    devuelve la que ya exista y, si no hay, origen 'pendiente' sin llamar a la IA. Si la IA está desactivada o falla, origen != 'ia'."""
    la, lo, _ = _point(place, lat, lon)
    s, cfg = account.config_for(request)
    if not s["ai_enabled"]:
        return {"origen": "desactivada"}
    if cfg.provider == "server" and not os.environ.get("OPENROUTER_API_KEY"):
        return {"origen": "sin_ia"}
    if not solo_cache:
        limit(request, "ai")
    try:
        return await ai_forecast(H(request), la, lo, cfg, cached_only=solo_cache)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e


_reverse: dict[tuple, tuple[float, dict]] = {}


@app.get("/api/reverse")
async def reverse(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Nombre del sitio donde estás (geocodificación inversa de OpenStreetMap/Nominatim, con caché). Sin él, se usa el locator."""
    k = (round(lat, 3), round(lon, 3))
    hit = _reverse.get(k)
    if hit and time.time() - hit[0] < 7 * 86400:
        return hit[1]
    limit(request, "reverse")
    out = {"name": None, "detalle": None, "locator": radio.to_locator(lat, lon)}
    try:
        r = await H(request).get("https://nominatim.openstreetmap.org/reverse", timeout=8, headers={"User-Agent": "MeteoGuilda/1.0 (+https://radio.guildawork.com)"},
                                 params={"format": "jsonv2", "lat": f"{lat:.4f}", "lon": f"{lon:.4f}", "zoom": 14, "accept-language": "es"})
        r.raise_for_status()
        a = r.json().get("address", {})
        town = next((a[x] for x in ("village", "town", "city", "municipality", "suburb", "hamlet", "county") if a.get(x)), None)
        region = a.get("state") or a.get("region")
        out.update(name=town or region, detalle=", ".join(x for x in (town, a.get("county") if a.get("county") != town else None, region, a.get("country")) if x))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("geocodificación inversa: %s", e)
        return out                        # sin nombre: no se cachea
    _reverse[k] = (time.time(), out)
    if len(_reverse) > 2000:
        _reverse.pop(next(iter(_reverse)))
    return out


@app.get("/api/air")
async def air(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Calidad del aire (EAQI), índice UV y polen del punto, ahora y 24 h."""
    limit(request, "point")
    try:
        return await airq.fetch(H(request), lat, lon)
    except httpx.HTTPError as e:
        raise HTTPException(502, "Fuente de calidad del aire no disponible") from e


@app.get("/api/compare")
async def compare(request: Request, pts: str = Query(max_length=400)):
    """Compara de 2 a 4 lugares ('lat,lon,nombre;lat,lon,nombre'): cifras de 48 h, series superponibles y un veredicto."""
    limit(request, "point")
    items = []
    try:
        for t in [x for x in pts.split(";") if x][:4]:
            la, lo, *n = t.split(",")
            la, lo = float(la), float(lo)
            if not (-90 <= la <= 90 and -180 <= lo <= 180):
                raise ValueError
            items.append((la, lo, (",".join(n) or f"{la:.2f}, {lo:.2f}")[:60]))
    except ValueError as e:
        raise HTTPException(422, "Formato: lat,lon,nombre;lat,lon,nombre") from e
    if len(items) < 2:
        raise HTTPException(422, "Indica al menos 2 lugares")
    http = H(request)
    try:
        sums = await asyncio.gather(*(get_summary(http, la, lo) for la, lo, _ in items))
    except httpx.HTTPError as e:
        raise HTTPException(502, "Fuente de modelos no disponible") from e
    filas = [comparar_mod.fila(n, la, lo, s) for (la, lo, n), s in zip(items, sums)]
    return {"lugares": filas, "veredicto": comparar_mod.veredicto(filas),
            "nota": "Mismos modelos y mismas horas locales de cada lugar; las series se alinean por tramo desde ahora, no por reloj común si hay husos distintos."}


@app.get("/api/mar")
async def mar(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Estado del mar del punto (oleaje, mar de fondo, periodo, agua) ahora y 48 h. `hay_mar=false` si está tierra adentro."""
    limit(request, "point")
    try:
        return await mar_mod.fetch(H(request), lat, lon)
    except httpx.HTTPError as e:
        raise HTTPException(502, "Fuente de oleaje no disponible") from e


@app.get("/api/activities")
def activities_list():
    return [{"id": k, "nombre": v["label"], "franja_h": list(v["horas"])} for k, v in activities.ACTIVITIES.items()]


@app.get("/api/activity")
async def activity(request: Request, activity: str, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                   min_score: int = Query(65, ge=30, le=95)):
    """Mejores ventanas de los próximos días para una actividad (senderismo, ciclismo, dron, colada, estrellas, playa) en ese punto."""
    limit(request, "point")
    try:
        return {**activities.plan(await get_summary(H(request), lat, lon), activity, min_score), "ubicacion": {"lat": lat, "lon": lon}}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, "Fuente de modelos no disponible") from e


ASSISTANT_SYSTEM = """Eres el asistente de Meteo Guilda · Radio Guilda: una app de previsión meteorológica con datos abiertos (varios modelos \
numéricos y observaciones reales de estaciones APRS, METAR y XEMA), rutas con el tiempo a la hora de paso y herramientas de radioafición \
(repetidores, propagación, satélites, cuaderno QSL). Responde en español, claro y breve.
Formato: puedes usar Markdown sencillo (**negrita** para lo importante, listas con guiones, títulos con ## solo si la respuesta es larga, tablas pequeñas para comparar horas). Nada de HTML.
Reglas:
- Para cifras meteorológicas usa SOLO los datos del bloque «Datos del lugar» o los resultados de las herramientas; si no tienes datos del lugar, búscalo tú con las herramientas por su nombre. No inventes cifras.
- Si la confianza es baja o los modelos discrepan, dilo. Si hay `contraste`, mira si lo que miden las estaciones reales ahora encaja con los modelos y, si no, da más peso a lo observado en las primeras horas. No sustituyes a los avisos oficiales (AEMET, Meteocat, Météo-France): recuérdalo en montaña, mar o actividades de riesgo.
- En radioafición puedes explicar conceptos y usar los repetidores del bloque de datos si aparece; no inventes frecuencias ni indicativos.
- Si la persona dice «aquí», «donde estoy» o «mi ubicación», usa el bloque «Datos de Mi ubicación» (si no existe, pídele que la active con el botón «Mi ubicación» de la app).
- Si te piden algo ajeno a la meteorología, la ruta o la radio, responde con brevedad y vuelve al tema."""


class ChatMsg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatReq(BaseModel):
    messages: list[ChatMsg] = Field(min_length=1, max_length=40)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    name: str | None = Field(None, max_length=80)
    section: str | None = Field(None, max_length=20)
    home: account.HomeIn | None = None          # «Mi ubicación» (si no llega, se usa la guardada en los ajustes)


@app.post("/api/assistant")
async def assistant(request: Request, req: ChatReq):
    """Chat con el asistente de IA (proveedor y modelo de los ajustes de la persona). Recibe el lugar que está mirando para dar datos reales."""
    return await _asistente(request, req)


@app.post("/api/assistant/stream")
async def assistant_stream(request: Request, req: ChatReq):
    """Lo mismo que /api/assistant pero en flujo (SSE): avisa de cada paso (herramientas que consulta) y termina con la respuesta completa.
    Eventos: {t:'estado'|'herr', ...}, {t:'fin', answer, model, herramientas, lugares, tarjetas} o {t:'error', status, detail}."""
    cola: asyncio.Queue = asyncio.Queue()

    async def correr():
        try:
            await cola.put({"t": "fin", **(await _asistente(request, req, cola.put_nowait))})
        except HTTPException as e:
            await cola.put({"t": "error", "status": e.status_code, "detail": e.detail if isinstance(e.detail, str) else "Error"})
        except Exception as e:  # noqa: BLE001
            log.warning("asistente (flujo): %s", e)
            await cola.put({"t": "error", "status": 500, "detail": "Error interno del asistente"})

    async def flujo():
        tarea = asyncio.create_task(correr())
        try:
            yield ": ok\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(cola.get(), 15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False, separators=(',', ':'))}\n\n"
                if ev["t"] in ("fin", "error"):
                    return
        finally:
            tarea.cancel()
    return StreamingResponse(flujo(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})


async def _asistente(request: Request, req: ChatReq, aviso=None) -> dict:
    """Cuerpo del asistente. `aviso(evento)` (opcional) recibe el progreso para el flujo."""
    aviso = aviso or (lambda _e: None)
    s, cfg = account.config_for(request)
    if not s["ai_enabled"]:
        raise HTTPException(403, "La IA está desactivada en tus ajustes")
    if cfg.provider == "server" and not os.environ.get("OPENROUTER_API_KEY"):
        raise HTTPException(503, "El servidor no tiene IA configurada; añade tu propia clave en Ajustes")
    limit(request, "assistant")
    await asyncio.to_thread(uso_ia.comprobar, account.current(request)["sub"], cfg.api_key)       # tope diario con la IA del servidor
    http = H(request)
    system = ASSISTANT_SYSTEM + f"\nHoy es {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC."
    home = req.home.model_dump() if req.home else s.get("home")
    sub_usuario = account.current(request)["sub"]
    system += memoria.contexto(sub_usuario)               # preferencias que la persona pidió recordar (opt-in; vacío si la memoria está apagada)
    ctx_datos: dict[str, dict] = {}       # datos ya cargados del lugar que se mira y de «Mi ubicación» (para la tarjeta)

    async def block(title: str, lat: float, lon: float) -> str:
        data = await build_forecast(http, lat, lon)
        ctx_datos[f"{lat:.4f},{lon:.4f}"] = data["datos"]
        return f"\n\n{title} (lat {lat:.3f}, lon {lon:.3f}), medias entre modelos:\n" + json.dumps(ai.ai_view(data["datos"], 12), ensure_ascii=False, separators=(",", ":"))
    try:
        viewing = req.lat is not None and req.lon is not None
        if viewing:
            system += await block(f"Datos del lugar «{req.name or 'punto'}» que está viendo", req.lat, req.lon)
        if home and not (viewing and abs(home["lat"] - req.lat) < 0.02 and abs(home["lon"] - req.lon) < 0.02):
            system += await block(f"Datos de Mi ubicación «{home.get('name') or 'sin nombre'}»", home["lat"], home["lon"])
        elif home:
            system += f"\n(El lugar que está viendo es su ubicación: «{home.get('name') or 'Mi ubicación'}».)"
        c0 = (req.lat, req.lon) if viewing else ((home["lat"], home["lon"]) if home else None)
        if req.section == "radio" and c0:
            reps = await asyncio.to_thread(radio.repeaters_near, c0[0], c0[1], 50, None, None, 12)
            system += "\n\nRepetidores cercanos:\n" + json.dumps(
                [{k: r.get(k) for k in ("name", "callsign", "band", "freq_mhz", "shift_khz", "tone_hz", "mode", "network", "dist_km")} for r in reps],
                ensure_ascii=False, separators=(",", ":"))
    except Exception as e:  # noqa: BLE001
        log.warning("contexto del asistente: %s", e)
    msgs = [{"role": m.role, "content": m.content} for m in req.messages[-14:]]
    if msgs[0]["role"] != "user":
        msgs = msgs[1:] or msgs
    free = cfg.provider == "server" and not cfg.model
    elegido: list[str] = []          # el modelo que respondió: se reutiliza en las vueltas siguientes

    async def ask(system_: str, msgs_: list[dict]) -> tuple[str, str]:
        last: Exception | None = None
        modelos = elegido[:1] or ([cfg.model] if not free else await ai.ranked_free_models(http, 5))
        for model in modelos:
            try:
                text = await ai.complete(http, cfg, system_, msgs_, model=model, max_tokens=800, temperature=0.3, timeout=60)
                if text.strip():
                    if free:
                        ai.note(model, None)
                    elegido[:] = [model]
                    return text, model
                if free:
                    ai.note(model, "vacio")
            except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError) as e:
                last = e
                if free:
                    ai.note(model, ai.kind_of(e))
                log.warning("asistente %s/%s: %s", cfg.provider, model, e)
        if elegido and not last:
            elegido.clear()
        raise HTTPException(502, f"La IA no ha respondido: {ai.friendly_error(last)}" if last else "La IA no ha respondido")

    env = assistant_tools.Entorno(
        app=request.app, http=http,
        ip=(request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip(),
        viendo={"name": req.name or "punto", "lat": req.lat, "lon": req.lon} if req.lat is not None and req.lon is not None else None,
        casa={"name": home.get("name") or "Mi ubicación", "lat": home["lat"], "lon": home["lon"]} if home else None,
        memoria=bool((s.get("memoria") or {}).get("activa")))
    usadas: list[dict] = []
    pregunta = req.messages[-1].content            # la última pregunta de la persona (msgs crece con los resultados de herramientas)
    system += assistant_tools.catalogo_texto()
    for paso in range(assistant_tools.MAX_PASOS):
        ultima = paso == assistant_tools.MAX_PASOS - 1
        text, model = await ask(system, msgs)
        llamadas = assistant_tools.parse(text)
        if not llamadas or ultima:
            respuesta = assistant_tools.limpiar(text)
            if respuesta:
                tarjetas = [r["tarjeta"] for r in usadas if r.get("tarjeta")][:3]
                uso = assistant_tools.resumen_uso(usadas)
                # el modelo contesta con los datos del lugar abierto sin llamar a herramientas: la tarjeta sale igual, de esos mismos datos
                if not any(t["tipo"] == "prevision" for t in tarjetas) and assistant_tools.es_pregunta_de_tiempo(pregunta):
                    lug = assistant_tools.lugar_de_contexto(pregunta, env)
                    datos = ctx_datos.get(f"{lug['lat']:.4f},{lug['lon']:.4f}") if lug else None
                    card = assistant_tools.tarjeta_prevision(lug, datos) if datos else None
                    if card:
                        tarjetas = [card, *tarjetas][:3]
                        uso = [{"nombre": "prevision", "etiqueta": "Previsión de modelos", "lugar": lug["name"], "ok": True}, *uso]
                        env.usados.insert(0, {"name": lug["name"], "lat": round(lug["lat"], 4), "lon": round(lug["lon"], 4)})
                return {"answer": respuesta, "model": model, "herramientas": uso, "lugares": env.usados[:6], "tarjetas": tarjetas}
            raise HTTPException(502, "La IA no ha respondido")
        aviso({"t": "herr", "items": [{"nombre": c["name"], "etiqueta": assistant_tools.ETIQUETAS.get(c["name"], c["name"])} for c in llamadas]})
        res = await assistant_tools.ejecutar_todas(env, llamadas)
        usadas += res
        msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": assistant_tools.respuesta_para_modelo(llamadas, res, paso == assistant_tools.MAX_PASOS - 2)}]
    raise HTTPException(502, "La IA no ha respondido")


@app.get("/api/raw")
async def raw(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Datos de modelos y balizas SIN texto de IA (para clientes MCP y modelos externos)."""
    limit(request, "point")
    ctx = ai.build_context(await get_summary(H(request), lat, lon), await nearby_stations(lat, lon, limit_n=12, http=H(request)))
    return {"datos": ctx, "unidades": {"viento": "km/h", "temp": "°C", "lluvia": "mm/3h", "nieve": "cm/3h",
                                         "isocero": "m", "presion": "hPa", "cape": "J/kg"},
            "nota": "Media entre los modelos de 'modelos' (ECMWF IFS y AIFS, DWD ICON, NOAA GFS, Météo-France, ECCC GEM, UK Met Office, KNMI); "
                    "'confianza' según su dispersión. 'por_modelo' trae el valor de cada uno. Observaciones: APRS, METAR y XEMA."}


@app.get("/api/stations")
async def stations(south: float = -90, west: float = -180, north: float = 90, east: float = 180, max_age_min: int = 120):
    try:
        obs = await asyncio.to_thread(store.latest, max_age_min * 60, (south, west, north, east))
    except Exception:  # noqa: BLE001
        return []
    flags = qc.quality_flags(obs)
    return [{**asdict(o), "quality": flags[o.callsign]} for o in obs]


@app.get("/api/observations")
async def observations(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                       km: float = Query(40, gt=0, le=150)):
    return await nearby_stations(lat, lon, km, 15, http=H(request))


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
    s_cfg = account.config_for(request) if req.ia else None
    if req.ia and s_cfg[0]["ai_enabled"]:
        limit(request, "ai_route")
        ctx = {"modo": res["modo"], "distancia_km": res["distancia_km"], "duracion_min": res["duracion_min"],
               "ascenso_m": res["ascenso_m"], "descenso_m": res["descenso_m"], "salida": res["salida"],
               "nivel_global": res["nivel_global"], "nivel_texto": res["nivel_texto"],
               "muestras": [{k: m.get(k) for k in ("d_km", "ele", "t", "nivel", "motivos", "meteo")} for m in res["muestras"]],
               "ventanas": res["ventanas"]}
        out = await ai.generate(ctx, H(request), system=ai.ROUTE_SYSTEM, trim=False, cfg=s_cfg[1])
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
async def repeaters(bbox: str | None = None, band: str | None = None, mode: str | None = None, q: str | None = Query(None, max_length=40),
                    coverage: bool = False, limit: int = Query(3000, le=5000)):
    """Repetidores (BrandMeister + HearHam + manuales). bbox = sur,oeste,norte,este."""
    try:
        return await asyncio.to_thread(radio.list_repeaters, _bbox(bbox), band, mode, q, limit, coverage)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e


@app.get("/api/repeaters/near")
async def repeaters_near(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), km: float = Query(60, le=300),
                         mode: str | None = None, band: str | None = None, limit: int = Query(60, le=300)):
    try:
        return await asyncio.to_thread(radio.repeaters_near, lat, lon, km, mode, band, limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e


@app.get("/api/repeaters/stats")
async def repeaters_stats():
    return await asyncio.to_thread(radio.repeater_stats)


@app.get("/api/repeaters/{rep_id}")
async def repeater(rep_id: int):
    r = await asyncio.to_thread(radio.get_repeater, rep_id)
    if not r:
        raise HTTPException(404, "Repetidor no encontrado")
    return r


@app.post("/api/repeaters/{rep_id}/coverage")
async def repeater_coverage_public(request: Request, rep_id: int):
    """Calcula (y guarda) la cobertura estimada si aún no existe. Limitado por IP."""
    r = await asyncio.to_thread(radio.get_repeater, rep_id, False)
    if not r:
        raise HTTPException(404, "Repetidor no encontrado")
    if not r["has_coverage"]:
        limit(request, "coverage")
        try:
            await radio.compute_coverage(H(request), rep_id)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Servicio de elevaciones no disponible: {e}") from e
    return await asyncio.to_thread(radio.get_repeater, rep_id)


# ---------- satélites, sol/luna, operación portátil ----------
_sat_cache: dict[tuple, tuple[float, list]] = {}
DEFAULT_SATS = ("ISS (ZARYA)", "SO-50", "RS-44", "AO-7", "AO-73", "IO-117", "CAS-4A", "CAS-4B", "AO-95", "PO-101", "AO-27")


@app.get("/api/satellites")
async def satellites_list(request: Request):
    return satellites.catalog(await satellites.load_tles(H(request)))


async def _sat_passes(http: httpx.AsyncClient, lat: float, lon: float, hours: float, min_el: float, sats: str | None = None, alt_m: float = 0):
    """(satélites elegidos, pasos) con caché de 5 min; por defecto, la ISS y los de aficionados más usados."""
    tles = await satellites.load_tles(http)
    if sats:
        want = {int(x) for x in sats.split(",") if x.strip().isdigit()}
        chosen = [t for t in tles if t["norad"] in want][:10]
    else:
        chosen = [t for t in tles if any(t["name"].upper().startswith(n) for n in DEFAULT_SATS)][:8]
    if not chosen:
        raise HTTPException(503, "No hay datos orbitales (TLE) disponibles ahora")
    key = (round(lat, 1), round(lon, 1), hours, min_el, tuple(t["norad"] for t in chosen), int(time.time() // 300))
    hit = _sat_cache.get(key)
    if not hit:
        now = datetime.now(timezone.utc)

        def calc():
            out = []
            for t in chosen:
                out += satellites.passes(t, lat, lon, now, hours, min_el, alt_m)
            return sorted(out, key=lambda p: p["aos"])
        res = await asyncio.to_thread(calc)
        if len(_sat_cache) > 200:
            _sat_cache.clear()
        hit = _sat_cache[key] = (time.time(), res)
    return chosen, hit[1]


@app.get("/api/satellites/passes")
async def satellites_passes(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                            hours: float = Query(24, gt=0, le=48), min_el: float = Query(10, ge=0, le=80), sats: str | None = None,
                            alt_m: float = Query(0, ge=-500, le=9000)):
    """Próximos pasos de satélites de aficionados (por defecto ISS y los FM/lineales más usados) sobre un punto."""
    limit(request, "sat")
    chosen, pasos = await _sat_passes(H(request), lat, lon, hours, min_el, sats, alt_m)
    return {"observador": {"lat": lat, "lon": lon, "alt_m": alt_m}, "satelites": [{"norad": t["norad"], "nombre": t["name"]} for t in chosen],
            "pasos": pasos, "nota": "Horas en ISO 8601 UTC. Elevaciones en grados sobre el horizonte; las frecuencias activas "
            "cambian: consulta AMSAT/ARISS.", "iss": satellites.ISS_INFO}


@app.get("/api/astro")
def astro_info(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), when: datetime | None = None):
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return astro.sun_moon(lat, lon, when)


_portable_cache: dict[tuple, tuple[float, dict]] = {}


@app.get("/api/portable")
async def portable_window(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    """Mejores ventanas para operar al aire libre (SOTA/POTA/campo) y horas con riesgo de tormenta o rachas fuertes."""
    k = _key(lat, lon)
    hit = _portable_cache.get(k)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    limit(request, "portable")
    try:
        raw = (await openmeteo.fetch_many([(lat, lon)], days=3, client=H(request)))[0]
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fuente de modelos no disponible: {e}") from e
    out = portable.window(openmeteo.hourly_consensus(raw), lat, lon)
    out["ubicacion"] = {"lat": lat, "lon": lon, "locator": radio.to_locator(lat, lon), "altitud_m": raw.get("elevation")}
    _portable_cache[k] = (time.time(), out)
    return out


_ventanas_cache: dict[tuple, tuple[float, dict]] = {}


@app.get("/api/radio/ventanas")
async def radio_ventanas(request: Request, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), modo: str = Query("fijo", pattern="^(fijo|portatil)$")):
    """Cuándo operar en las próximas 48 h (tramos de 3 h): HF, tropo VHF/UHF, satélites y, en modo portátil, el tiempo para la antena."""
    k = (_key(lat, lon), modo)
    hit = _ventanas_cache.get(k)
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    limit(request, "portable")
    http = H(request)
    try:
        raw, trp, sol = await asyncio.gather(openmeteo.fetch_many([(lat, lon)], days=3, client=http), radio.propagation(http, lat, lon), radio.solar(http), return_exceptions=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, "Fuente de datos no disponible") from e
    if isinstance(raw, Exception):
        raise HTTPException(502, f"Fuente de modelos no disponible: {raw}")
    try:
        _, pasos = await _sat_passes(http, lat, lon, 48, 15)
    except HTTPException:
        pasos = []
    win = portable.window(openmeteo.hourly_consensus(raw[0]), lat, lon)
    out = ventanas_mod.construir(lat, lon, ahora=datetime.now(timezone.utc), horas_campo=win["horas"], tropo_serie=None if isinstance(trp, Exception) else trp,
                                 solar={} if isinstance(sol, Exception) else sol, pasos=pasos, modo=modo)
    out["ubicacion"] = {"lat": lat, "lon": lon, "locator": radio.to_locator(lat, lon), "altitud_m": raw[0].get("elevation")}
    out["fuentes"] = {"modelos": True, "tropo": not isinstance(trp, Exception), "solar": not isinstance(sol, Exception) and sol.get("kp") is not None, "satelites": bool(pasos)}
    if len(_ventanas_cache) > 200:
        _ventanas_cache.clear()
    _ventanas_cache[k] = (time.time(), out)
    return out


# ---------- perfiles y cuaderno de contactos (clave secreta Bearer) ----------
def need_profile(authorization: str | None = Header(None)) -> str:
    key = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    try:
        h = logbook.auth(key)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e
    if not h:
        raise HTTPException(401, "Falta la clave del cuaderno (Authorization: Bearer <clave>)")
    return h


@app.post("/api/profile", status_code=201)
def profile_create(request: Request):
    """Crea un perfil anónimo y devuelve su clave secreta (solo se muestra una vez)."""
    limit(request, "profile")
    return {"key": logbook.create_profile(), "aviso": "Guarda la clave: quien la tenga puede leer y modificar el cuaderno."}


@app.get("/api/profile")
def profile_get(h: str = Depends(need_profile)):
    return {"settings": logbook.get_settings(h)}


@app.put("/api/profile")
def profile_put(settings: dict, h: str = Depends(need_profile)):
    try:
        return {"settings": logbook.put_settings(h, settings)}
    except ValueError as e:
        raise HTTPException(413, str(e)) from e


@app.delete("/api/profile", status_code=204)
def profile_delete(h: str = Depends(need_profile)):
    logbook.delete_profile(h)


class QsoIn(BaseModel):
    call: str
    qso_at: str
    band: str | None = None
    mode: str | None = None
    freq_mhz: float | None = None
    rst_sent: str | None = None
    rst_rcvd: str | None = None
    grid: str | None = None
    name: str | None = None
    notes: str | None = Field(None, max_length=500)
    qsl_sent: bool = False
    qsl_rcvd: bool = False


class QsoPatch(BaseModel):
    qsl_sent: bool | None = None
    qsl_rcvd: bool | None = None
    notes: str | None = Field(None, max_length=500)
    name: str | None = None
    grid: str | None = None
    rst_sent: str | None = None
    rst_rcvd: str | None = None


@app.get("/api/qso")
def qso_list(h: str = Depends(need_profile), q: str | None = None, band: str | None = None, mode: str | None = None,
             status: str | None = Query(None, pattern="^(pending|confirmed)$"), limit: int = Query(200, le=1000), offset: int = 0):
    return logbook.list_qsos(h, q, band, mode, status, limit, offset)


@app.post("/api/qso", status_code=201)
def qso_add(q: QsoIn, h: str = Depends(need_profile)):
    try:
        qs = logbook.clean(q.model_dump())
        dup = logbook.worked_before(h, qs["call"])
        return {"id": logbook.add_many(h, [qs])[0], "ya_trabajado": [{"qso_at": d["qso_at"], "band": d["band"], "mode": d["mode"]} for d in dup][:5]}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/qso/stats")
def qso_stats(h: str = Depends(need_profile)):
    qth = (logbook.get_settings(h) or {}).get("qth")
    return logbook.stats(h, qth)


@app.get("/api/qso/adif")
def qso_adif(h: str = Depends(need_profile)):
    s = logbook.get_settings(h) or {}
    rows = logbook.list_qsos(h, limit=1000)
    return Response(logbook.to_adif(rows, (s.get("qth") or {}).get("locator")), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="radioguilda.adi"'})


@app.post("/api/qso/import")
async def qso_import(request: Request, h: str = Depends(need_profile)):
    """Importa un ADIF (cuerpo de texto). Devuelve cuántos contactos se añadieron."""
    text = (await request.body()).decode("utf-8", "replace")
    try:
        recs = logbook.parse_adif(text)
        ids = await asyncio.to_thread(logbook.add_many, h, recs)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"importados": len(ids)}


@app.get("/api/qso/worked")
def qso_worked(call: str = Query(max_length=14), h: str = Depends(need_profile)):
    return logbook.worked_before(h, call)


@app.patch("/api/qso/{qid}")
def qso_patch(qid: int, p: QsoPatch, h: str = Depends(need_profile)):
    if not logbook.update(h, qid, p.model_dump(exclude_none=True)):
        raise HTTPException(404, "Contacto no encontrado")
    return {"ok": True}


@app.delete("/api/qso/{qid}", status_code=204)
def qso_delete(qid: int, h: str = Depends(need_profile)):
    if not logbook.delete(h, qid):
        raise HTTPException(404, "Contacto no encontrado")


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


@app.get("/api/admin/estado")
async def admin_estado(x_admin_token: str | None = Header(None)):
    """Salud de las fuentes y procesos de fondo (APRS, AEMET, Meteocat, verificación, push). Solo agregados."""
    _admin(x_admin_token)
    return await asyncio.to_thread(estado_mod.estado)


@app.post("/api/admin/repeaters")
async def add_repeater(rep: RepIn, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    return {"id": await asyncio.to_thread(radio.upsert_repeater, rep.model_dump())}


@app.post("/api/admin/repeaters/sync")
async def repeaters_sync(request: Request, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    return await repeater_sources.sync(H(request))


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
