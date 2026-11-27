"""Servidor MCP de Meteo Guilda: expone los datos a modelos de IA (Claude, GPT, etc.).

Es un cliente fino de la API REST (mismas cachés y límites). Transportes:
  python -m meteoguilda.mcp_server            -> stdio (Claude Desktop / Claude Code local)
  python -m meteoguilda.mcp_server --http     -> HTTP streamable en :8001/mcp (token con MG_MCP_TOKEN)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
import time
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

API = os.environ.get("MG_API", "http://127.0.0.1:8000")


class ErrorLegible(ToolError, RuntimeError):
    """Error que el modelo SÍ ve con su texto (el SDK 2 oculta el de las excepciones genéricas tras «Error executing tool …»)."""

mcp = MCPServer(
    "Meteo Guilda / Radio Guilda",
    instructions=(
        "Datos meteorológicos SIN cocinar: media de modelos numéricos abiertos (ECMWF IFS y AIFS, ICON, GFS, Météo-France, GEM, UK Met Office, KNMI; 'por_modelo' trae cada uno para contrastar) y "
        "observaciones reales (balizas APRS con control de calidad, METAR y estaciones XEMA de Cataluña). Usa get_forecast_data para razonar tú mismo con los datos; "
        "get_ai_forecast devuelve además un resumen automático por reglas. Cita siempre la confianza, no inventes "
        "cifras y recuerda consultar los avisos oficiales (AEMET, Meteocat, Météo-France) en actividades de riesgo. "
        "Los niveles de riesgo de plan_route son 0 favorable, 1 precaución, 2 desaconsejable. Radio Guilda añade repetidores "
        "(BrandMeister y listados abiertos), propagación, satélites, ventanas para operar en portátil y un cuaderno de contactos "
        "(QSO/QSL) que requiere la clave del cuaderno (parámetro key o variable MG_LOGBOOK_KEY). Además: get_air_quality (aire, UV, polen), plan_activity "
        "(mejores ventanas para senderismo, ciclismo, dron, colada, estrellas o playa) y, con el inicio de sesión (OAuth), herramientas de la cuenta de la persona: "
        "get_my_location / get_forecast_here («mi ubicación»), ajustes de IA y ask_assistant (consume tokens de su proveedor)."),
)
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=API, timeout=60, headers={"User-Agent": "MeteoGuilda-MCP/0.2"})
    return _client


def _auth(key: str | None) -> dict:
    k = key or os.environ.get("MG_LOGBOOK_KEY")
    if not k:
        raise ErrorLegible("Falta la clave del cuaderno: pásala en `key` o define MG_LOGBOOK_KEY")
    return {"Authorization": f"Bearer {k}"}


def _who(ctx: Context | None) -> dict | None:
    """Persona que se autenticó con OAuth en esta petición (la pone el middleware en cabeceras internas); None con la clave fija."""
    try:
        h = ctx.request_context.request.headers
    except Exception:  # noqa: BLE001
        return None
    sub = h.get("x-mg-sub")
    return {"sub": sub, "email": h.get("x-mg-email", "")} if sub else None


def _need_who(ctx: Context | None) -> dict:
    w = _who(ctx)
    if not w:
        raise ErrorLegible("Esta herramienta es de tu cuenta: conéctate con el inicio de sesión de Guilda Work (OAuth). La clave fija del MCP no identifica a nadie.")
    return w


async def _as(who: dict, method: str, path: str, **kw) -> Any:
    """Llamada a la API en nombre de la persona (usa su proveedor de IA, sus ajustes y su ubicación)."""
    kw["headers"] = {**kw.get("headers", {}), "X-Admin-Token": os.environ.get("MG_ADMIN_TOKEN", ""), "X-Act-As": who["sub"], "X-Act-Email": who.get("email", "")}
    return await _call(method, path, **kw)


async def _call(method: str, path: str, **kw) -> Any:
    if kw.get("params"):
        kw["params"] = {k: v for k, v in kw["params"].items() if v is not None}
    r = await client().request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ErrorLegible(f"API {r.status_code}: {detail}")
    if r.status_code == 204 or not r.content:
        return {}
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"texto": r.text}


# ---------- respuestas grandes por enlace (ver descargas.py) ----------
UMBRAL_ENLACE = 60_000          # bytes de JSON: por encima, la respuesta va en un fichero por enlace y el modelo recibe un resumen


async def _enlace(ctx: Context | None, nombre: str, tipo: str, contenido: str) -> dict:
    """Guarda el contenido en la API y devuelve el enlace firmado (caduca en una hora)."""
    w = _who(ctx) if ctx is not None else None
    return await _call("POST", "/api/descargas", headers={"X-Admin-Token": os.environ.get("MG_ADMIN_TOKEN", "")},
                       json={"nombre": nombre, "tipo": tipo, "contenido": contenido, "sub": (w or {}).get("sub")})


def _resumen(datos: Any) -> dict:
    if isinstance(datos, list):
        return {"elementos": len(datos), "primeros": datos[:5]}
    if isinstance(datos, dict):
        return {k: (f"{len(v)} elementos (en el fichero)" if isinstance(v, (list, dict)) and len(json.dumps(v, default=str)) > 2000 else v) for k, v in datos.items()}
    return {}


async def _quiza_enlace(ctx: Context | None, nombre: str, datos: Any) -> Any:
    """Si la respuesta es grande, la deja en un fichero JSON por enlace y devuelve un resumen; si no, la devuelve tal cual."""
    texto = json.dumps(datos, ensure_ascii=False, default=str)
    if len(texto.encode()) <= UMBRAL_ENLACE:
        return datos
    return {"respuesta_grande": True, "descarga": await _enlace(ctx, f"{nombre}.json", "application/json", texto), "resumen": _resumen(datos),
            "nota": "La respuesta completa es grande: está en el fichero del enlace (JSON). Aquí va un resumen; acota la consulta para recibirla entera."}


@mcp.tool()
async def list_places() -> list[dict]:
    """Los 6 lugares principales (Garòs, Premià de Mar, Tórtoles de Esgueva, Bresca, La Garrovilla,
    Bagnères-de-Bigorre) con una instantánea: temperatura, racha máxima y lluvia en 24 h."""
    return await _call("GET", "/api/places")


@mcp.tool()
async def search_place(query: str) -> list[dict]:
    """Busca cualquier lugar por nombre y devuelve coordenadas y altitud (para usarlas en otras herramientas)."""
    return await _call("GET", "/api/search", params={"q": query})


@mcp.tool()
async def get_forecast_data(lat: float, lon: float, ctx: Context = None) -> dict:
    """Datos previstos crudos en tramos de 3 h hasta 5 días: temperatura, lluvia, nieve, viento y rachas (km/h),
    dirección, nubes, humedad, presión, CAPE, isocero, con rango entre modelos y confianza, más balizas APRS
    cercanas. Sin texto de IA: interprétalos tú. Si la respuesta es muy grande, llega como enlace de descarga (JSON) con un resumen."""
    return await _quiza_enlace(ctx, "prevision", await _call("GET", "/api/raw", params={"lat": lat, "lon": lon}))


@mcp.tool()
async def get_forecast_by_name(place_name: str) -> dict:
    """Busca un pueblo o ciudad por su nombre («Sitges», «Vielha», «Santa Cruz, Tenerife») y devuelve directamente sus datos previstos
    (como get_forecast_data) junto al lugar elegido y las demás coincidencias, por si hay homónimos. Evita encadenar search_place y get_forecast_data."""
    hits = await _call("GET", "/api/search", params={"q": place_name})
    if not hits:
        raise ErrorLegible(f"No he encontrado ningún lugar llamado «{place_name}»")
    h = hits[0]
    return {"lugar": h, "otras_coincidencias": hits[1:4], "datos": await _call("GET", "/api/raw", params={"lat": h["lat"], "lon": h["lon"]})}


@mcp.tool()
async def get_ai_forecast(place: str | None = None, lat: float | None = None, lon: float | None = None, refresh: bool = False) -> dict:
    """Resumen automático por reglas (resumen, riesgos, recomendaciones) más los datos de origen de todos los modelos y
    observaciones. La lectura redactada por IA la pide la web con los ajustes de cada persona; aquí razona tú con los datos. Pasa `place` (slug: garos, premia-de-mar, tortoles-de-esgueva, bresca, la-garrovilla,
    bagneres-de-bigorre) o lat+lon."""
    params = {"place": place} if place else {"lat": lat, "lon": lon}
    if refresh:                       # vuelve a consultar los modelos (como mucho una vez cada 5 min por lugar)
        params["refresh"] = 1
    return await _call("GET", "/api/forecast", params=params)


@mcp.tool()
async def get_nearby_observations(lat: float, lon: float, radius_km: float = 40) -> list[dict]:
    """Observaciones reales recientes cercanas: balizas APRS/CWOP (con control de calidad), METAR de aeropuertos y estaciones XEMA (Cataluña). Campo `fuente`."""
    return await _call("GET", "/api/observations", params={"lat": lat, "lon": lon, "km": radius_km})


@mcp.tool()
async def plan_route(waypoints: list[dict], mode: Literal["foot", "car"] = "foot", depart_iso: str | None = None,
                     with_ai_reading: bool = False, ctx: Context = None) -> dict:
    """Planifica una ruta a pie o en coche y evalúa la meteo a la hora de paso en cada punto.
    `waypoints`: lista de {"lat":…, "lon":…} (mínimo 2). `depart_iso`: salida en ISO 8601 con zona (máx. 72 h).
    Devuelve distancia, duración, desnivel, nivel de riesgo (0-2) por tramo con motivos, mejores ventanas
    de salida, balizas y repetidores cercanos al recorrido."""
    body = {"waypoints": waypoints, "mode": mode, "depart": depart_iso, "ia": with_ai_reading}
    res = await _call("POST", "/api/route", json=body)
    res.pop("geometria", None)  # pesada; el modelo no la necesita
    return await _quiza_enlace(ctx, "ruta", res)


@mcp.tool()
async def get_vhf_propagation(lat: float, lon: float) -> dict:
    """Propagación troposférica VHF/UHF prevista (gradiente de refractividad: normal, super-refracción, conducto)
    en las próximas 72 h, y locator Maidenhead del punto. Indicador orientativo."""
    return await _call("GET", "/api/propagation", params={"lat": lat, "lon": lon})


@mcp.tool()
async def get_hf_conditions() -> dict:
    """Condiciones solar-geomagnéticas actuales para HF: índice Kp, flujo solar (SFI) y lectura simplificada."""
    return await _call("GET", "/api/solar")


@mcp.tool()
async def find_repeaters(lat: float, lon: float, radius_km: float = 60) -> list[dict]:
    """Repetidores de radioaficionado cercanos: frecuencia, desplazamiento, subtono CTCSS, modo, distancia y si
    el punto está dentro de su cobertura estimada por línea de vista."""
    return await _call("GET", "/api/repeaters/near", params={"lat": lat, "lon": lon, "km": radius_km})


@mcp.tool()
async def maidenhead(lat: float | None = None, lon: float | None = None, locator: str | None = None) -> dict:
    """Convierte coordenadas a locator Maidenhead (p. ej. JN58td) o un locator a coordenadas centrales."""
    return await _call("GET", "/api/locator", params={"lat": lat, "lon": lon, "loc": locator})


@mcp.tool()
async def search_repeaters(query: str | None = None, lat: float | None = None, lon: float | None = None, radius_km: float = 60,
                           mode: Literal["FM", "DMR", "D-STAR", "C4FM"] | None = None, band: str | None = None, limit: int = 40, ctx: Context = None) -> Any:
    """Busca repetidores: por texto (indicativo o ciudad) o por cercanía (lat+lon+radius_km). Filtra por modo (FM, DMR, D-STAR,
    C4FM) y banda (2m, 70cm...). Datos de BrandMeister y listados abiertos (HearHam); incluye color code, subtono y desplazamiento."""
    if lat is not None and lon is not None:
        return await _quiza_enlace(ctx, "repetidores", await _call("GET", "/api/repeaters/near", params={"lat": lat, "lon": lon, "km": radius_km, "mode": mode, "band": band, "limit": limit}))
    return await _quiza_enlace(ctx, "repetidores", await _call("GET", "/api/repeaters", params={"q": query, "mode": mode, "band": band, "limit": limit}))


@mcp.tool()
async def get_repeater(repeater_id: int, compute_coverage: bool = False, ctx: Context = None) -> dict:
    """Ficha completa de un repetidor. Con compute_coverage=True calcula y guarda su cobertura estimada por línea de vista
    (tarda unos segundos) y devuelve el polígono GeoJSON."""
    if compute_coverage:
        return await _quiza_enlace(ctx, f"repetidor-{repeater_id}", await _call("POST", f"/api/repeaters/{repeater_id}/coverage"))
    return await _quiza_enlace(ctx, f"repetidor-{repeater_id}", await _call("GET", f"/api/repeaters/{repeater_id}"))


@mcp.tool()
async def repeater_stats() -> dict:
    """Resumen del catálogo de repetidores: total, por modo y por banda, fuentes y fecha de actualización."""
    return await _call("GET", "/api/repeaters/stats")


@mcp.tool()
async def satellite_passes(lat: float, lon: float, hours: float = 24, min_elevation: float = 10, norad_ids: list[int] | None = None, ctx: Context = None) -> dict:
    """Próximos pasos de satélites de aficionados (ISS, SO-50, RS-44, AO-7...) sobre un punto: AOS/LOS (UTC), elevación máxima,
    azimuts y traza para dibujar el cielo. Sin norad_ids usa los más habituales."""
    params = {"lat": lat, "lon": lon, "hours": hours, "min_el": min_elevation}
    if norad_ids:
        params["sats"] = ",".join(map(str, norad_ids))
    return await _quiza_enlace(ctx, "pasos-satelites", await _call("GET", "/api/satellites/passes", params=params))


@mcp.tool()
async def sun_moon(lat: float, lon: float, when_iso: str | None = None) -> dict:
    """Orto, ocaso, crepúsculos y horas de luz del Sol, y fase e iluminación de la Luna (útil para HF, rutas y astrofotografía)."""
    return await _call("GET", "/api/astro", params={"lat": lat, "lon": lon, "when": when_iso})


@mcp.tool()
async def portable_operation_window(lat: float, lon: float) -> dict:
    """Mejores ventanas para operar al aire libre (SOTA/POTA/campo) en las próximas 48 h, con riesgo por rachas (mástil), lluvia,
    tormentas (desconectar antenas) y noche."""
    return await _call("GET", "/api/portable", params={"lat": lat, "lon": lon})


@mcp.tool()
async def get_official_warnings(lat: float, lon: float) -> dict:
    """Avisos oficiales vigentes (AEMET/Meteoalerta) que afectan a un punto, con nivel (amarillo/naranja/rojo), zona, horas e instrucciones, más enlaces
    oficiales (AEMET, Meteocat, Météo-France, MeteoAlarm). `disponible=false` significa que el servidor no puede consultarlos: no es «sin avisos»."""
    return await _call("GET", "/api/avisos", params={"lat": lat, "lon": lon})


@mcp.tool()
async def radio_operating_windows(lat: float, lon: float, mode: str = "fijo") -> dict:
    """Cuándo conviene operar en las próximas 48 h, en tramos de 3 h: HF (luz, línea gris, estado solar), tropo VHF/UHF, pasos de satélites y,
    con mode="portatil" (POTA/SOTA/móvil), el tiempo para la antena. Notas 0-100 por capa, mejores tramos y bandas recomendadas."""
    return await _call("GET", "/api/radio/ventanas", params={"lat": lat, "lon": lon, "modo": mode})


@mcp.tool()
async def get_stations(south: float, west: float, north: float, east: float, max_age_min: int = 120, ctx: Context = None) -> Any:
    """Estaciones meteo APRS en un recuadro (con calidad: ok/range/outlier). Si son muchas, llegan como enlace de descarga (JSON) con un resumen."""
    return await _quiza_enlace(ctx, "estaciones", await _call("GET", "/api/stations", params={"south": south, "west": west, "north": north, "east": east, "max_age_min": max_age_min}))


@mcp.tool()
async def logbook_create_profile() -> dict:
    """Crea un cuaderno de contactos anónimo y devuelve su clave secreta (guárdala; solo se muestra una vez)."""
    return await _call("POST", "/api/profile")


@mcp.tool()
async def logbook_list(key: str | None = None, query: str | None = None, band: str | None = None, mode: str | None = None,
                       status: Literal["pending", "confirmed"] | None = None, limit: int = 50, ctx: Context = None) -> Any:
    """Lista contactos del cuaderno (QSO), con filtros. `status`: pending = sin QSL recibida, confirmed = con QSL recibida.
    Si la lista es muy grande, llega como enlace de descarga (JSON) con los primeros contactos."""
    return await _quiza_enlace(ctx, "contactos", await _call("GET", "/api/qso", headers=_auth(key), params={"q": query, "band": band, "mode": mode, "status": status, "limit": limit}))


@mcp.tool()
async def logbook_add(call: str, qso_at_iso: str, band: str | None = None, mode: str | None = None, freq_mhz: float | None = None,
                      rst_sent: str | None = "59", rst_rcvd: str | None = "59", grid: str | None = None, name: str | None = None,
                      notes: str | None = None, qsl_sent: bool = False, qsl_rcvd: bool = False, key: str | None = None) -> dict:
    """Añade un contacto al cuaderno. Avisa si ya habías trabajado ese indicativo."""
    body = {"call": call, "qso_at": qso_at_iso, "band": band, "mode": mode, "freq_mhz": freq_mhz, "rst_sent": rst_sent, "rst_rcvd": rst_rcvd,
            "grid": grid, "name": name, "notes": notes, "qsl_sent": qsl_sent, "qsl_rcvd": qsl_rcvd}
    return await _call("POST", "/api/qso", headers=_auth(key), json=body)


@mcp.tool()
async def logbook_mark_qsl(qso_id: int, sent: bool | None = None, received: bool | None = None, key: str | None = None) -> dict:
    """Marca un contacto como QSL enviada y/o recibida (confirmada)."""
    body = {k: v for k, v in {"qsl_sent": sent, "qsl_rcvd": received}.items() if v is not None}
    return await _call("PATCH", f"/api/qso/{qso_id}", headers=_auth(key), json=body)


@mcp.tool()
async def logbook_stats(key: str | None = None) -> dict:
    """Estadísticas del cuaderno: total, confirmados, indicativos y cuadros distintos, por banda/modo/mes, contacto más lejano."""
    return await _call("GET", "/api/qso/stats", headers=_auth(key))


@mcp.tool()
async def logbook_export_adif(key: str | None = None, ctx: Context = None) -> dict:
    """Exporta el cuaderno en formato ADIF como ENLACE DE DESCARGA (caduca en una hora): no devuelve el contenido. Enséñale el enlace a la persona."""
    adif = (await _call("GET", "/api/qso/adif", headers=_auth(key)))["texto"]
    return {**await _enlace(ctx, "radioguilda.adi", "text/plain; charset=utf-8", adif), "contactos": adif.upper().count("<EOR>")}


@mcp.tool()
async def logbook_import_adif(adif_text: str, key: str | None = None) -> dict:
    """Importa contactos desde el texto de un fichero ADIF."""
    return await _call("POST", "/api/qso/import", headers={**_auth(key), "content-type": "text/plain"}, content=adif_text.encode())


# ---------- Aire, actividades, ubicación y cuenta ----------
@mcp.tool()
async def get_air_quality(lat: float, lon: float) -> dict:
    """Calidad del aire (índice europeo EAQI), índice UV y polen de un punto: valores actuales, máximo de UV de hoy con consejo y próximas 24 h."""
    return await _call("GET", "/api/air", params={"lat": lat, "lon": lon})


@mcp.tool()
async def compare_places(places: list[dict]) -> dict:
    """Compara 2-4 lugares con los mismos modelos: temperatura ahora, mínima/máxima, lluvia, racha y nubes de las próximas 48 h, y un veredicto.
    `places` es una lista de {lat, lon, name}."""
    pts = ";".join(f"{float(p['lat'])},{float(p['lon'])},{str(p.get('name', '')).replace(';', ' ').replace(',', ' ')}" for p in places[:4])
    return await _call("GET", "/api/compare", params={"pts": pts})


@mcp.tool()
async def get_sea_state(lat: float, lon: float) -> dict:
    """Estado del mar de un punto costero: altura y periodo del oleaje, mar de fondo, dirección, temperatura del agua y estado Douglas, ahora y 48 h.
    `hay_mar=false` si el punto está tierra adentro."""
    return await _call("GET", "/api/mar", params={"lat": lat, "lon": lon})


@mcp.tool()
async def list_activities() -> list[dict]:
    """Actividades que sabe planificar `plan_activity` (senderismo, ciclismo, dron, colada, estrellas, playa)."""
    return await _call("GET", "/api/activities")


@mcp.tool()
async def plan_activity(activity: str, lat: float, lon: float, min_score: int = 65) -> dict:
    """Mejores ventanas de los próximos días para una actividad en un punto, con puntuación 0-100 por tramos de 3 h, motivos de las penalizaciones y datos de cada ventana.
    `activity`: senderismo, ciclismo, dron, colada, estrellas o playa."""
    return await _call("GET", "/api/activity", params={"activity": activity, "lat": lat, "lon": lon, "min_score": min_score})


@mcp.tool()
async def reverse_geocode(lat: float, lon: float) -> dict:
    """Nombre del sitio de unas coordenadas (municipio, comarca, región) y su locator Maidenhead."""
    return await _call("GET", "/api/reverse", params={"lat": lat, "lon": lon})


@mcp.tool()
async def get_snapshot(points: list[dict]) -> list[dict]:
    """Instantánea (temperatura, racha, lluvia 24 h, confianza) de hasta 15 puntos [{lat, lon}, …] de una vez: ideal para comparar lugares."""
    pts = ";".join(f"{p['lat']},{p['lon']}" for p in points[:15])
    return await _call("GET", "/api/snapshot", params={"pts": pts})


@mcp.tool()
async def get_my_location(ctx: Context) -> dict:
    """«Mi ubicación» guardada en tu cuenta (la que fijas en la web con el botón Mi ubicación): {lat, lon, name} o null. Requiere iniciar sesión (OAuth)."""
    s = await _as(_need_who(ctx), "GET", "/api/me/settings")
    return {"home": s.get("home"), "email": s.get("email")}


@mcp.tool()
async def set_my_location(ctx: Context, lat: float | None = None, lon: float | None = None, name: str | None = None, clear: bool = False) -> dict:
    """Guarda (lat+lon, name opcional) o borra (`clear=true`) tu ubicación. La usan el asistente y get_forecast_here. Requiere OAuth."""
    body = {"home": None} if clear else {"home": {"lat": lat, "lon": lon, "name": name}}
    if not clear and (lat is None or lon is None):
        raise ErrorLegible("Indica lat y lon, o clear=true para borrarla")
    s = await _as(_need_who(ctx), "PUT", "/api/me/settings", json=body)
    return {"home": s.get("home")}


@mcp.tool()
async def get_forecast_here(ctx: Context, with_air: bool = False) -> dict:
    """Datos de modelos y observaciones (con `por_modelo`) de TU ubicación guardada; con `with_air=true` añade aire/UV/polen. Requiere OAuth y tener ubicación (set_my_location)."""
    w = _need_who(ctx)
    home = (await _as(w, "GET", "/api/me/settings")).get("home")
    if not home:
        raise ErrorLegible("No tienes ubicación guardada: usa set_my_location o el botón «Mi ubicación» de la web")
    out = {"ubicacion": home, "pronostico": await _call("GET", "/api/raw", params={"lat": home["lat"], "lon": home["lon"]})}
    if with_air:
        out["aire"] = await _call("GET", "/api/air", params={"lat": home["lat"], "lon": home["lon"]})
    return out


@mcp.tool()
async def get_account_settings(ctx: Context) -> dict:
    """Tus ajustes de la cuenta: IA interna sí/no, proveedor, modelo, qué claves hay guardadas (nunca su valor) y tu ubicación. Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/me/settings")


@mcp.tool()
async def set_ai_settings(ctx: Context, ai_enabled: bool | None = None, provider: Literal["server", "openrouter", "openai", "anthropic"] | None = None, model: str | None = None) -> dict:
    """Cambia tus ajustes de IA (activar/desactivar, proveedor, modelo). Las claves se guardan solo desde la web (no pasan por el MCP). Requiere OAuth."""
    body = {k: v for k, v in {"ai_enabled": ai_enabled, "provider": provider, "model": model}.items() if v is not None}
    return await _as(_need_who(ctx), "PUT", "/api/me/settings", json=body)


@mcp.tool()
async def generate_ai_forecast(ctx: Context, lat: float | None = None, lon: float | None = None, place: str | None = None) -> dict:
    """Pide la lectura redactada por la IA de tu cuenta (tu proveedor y modelo de Ajustes) para un lugar (`place` o lat+lon). CONSUME TOKENS de ese proveedor; la
    lectura vigente (3 h) se reutiliza sin coste. Requiere OAuth."""
    params = {"place": place} if place else {"lat": lat, "lon": lon}
    return await _as(_need_who(ctx), "GET", "/api/forecast/ai", params=params)


@mcp.tool()
async def ask_assistant(ctx: Context, question: str, lat: float | None = None, lon: float | None = None, name: str | None = None) -> dict:
    """Pregunta al asistente de Meteo Guilda (tu proveedor y modelo de Ajustes; consume tokens). Recibe los datos reales del lugar (lat+lon) y de tu ubicación
    guardada: «¿qué tiempo hace aquí?» usa tu ubicación. Requiere OAuth."""
    body = {"messages": [{"role": "user", "content": question}], "lat": lat, "lon": lon, "name": name}
    return await _as(_need_who(ctx), "POST", "/api/assistant", json={k: v for k, v in body.items() if v is not None})


# ---------- Resto de la API: satélites, perfil, cuaderno y cuenta ----------
@mcp.tool()
async def server_status() -> dict:
    """Estado del servicio ({ok, db})."""
    return await _call("GET", "/api/health")


@mcp.tool()
async def list_satellites(ctx: Context = None) -> Any:
    """Satélites de aficionados y de interés (ISS, etc.) con su identificador NORAD, para pasar a satellite_passes."""
    return await _quiza_enlace(ctx, "satelites", await _call("GET", "/api/satellites"))


@mcp.tool()
async def profile_get(key: str | None = None) -> dict:
    """Ajustes sincronizados del perfil de la clave del cuaderno: favoritos y QTH {favs, qth}. Es lo que usa la web con «sincronización»."""
    return await _call("GET", "/api/profile", headers=_auth(key))


@mcp.tool()
async def profile_set(favs: list[dict] | None = None, qth: dict | None = None, key: str | None = None) -> dict:
    """Guarda en el perfil de la clave del cuaderno los favoritos (lista de {name, lat, lon, sub?, pinned?}) y/o el QTH {lat, lon, locator?, call?}.
    Los campos que no pases se conservan."""
    cur = (await _call("GET", "/api/profile", headers=_auth(key))).get("settings") or {}
    if favs is not None:
        cur["favs"] = favs
    if qth is not None:
        cur["qth"] = qth
    return await _call("PUT", "/api/profile", headers=_auth(key), json=cur)


@mcp.tool()
async def logbook_worked(call: str, key: str | None = None) -> dict:
    """¿Ya has trabajado este indicativo? Devuelve los contactos anteriores (duplicados)."""
    return await _call("GET", "/api/qso/worked", headers=_auth(key), params={"call": call})


@mcp.tool()
async def logbook_update(qso_id: int, notes: str | None = None, name: str | None = None, grid: str | None = None, rst_sent: str | None = None,
                         rst_rcvd: str | None = None, qsl_sent: bool | None = None, qsl_rcvd: bool | None = None, key: str | None = None) -> dict:
    """Edita un contacto del cuaderno (notas, nombre, locator, RST, QSL). Solo cambian los campos que pases."""
    body = {k: v for k, v in {"notes": notes, "name": name, "grid": grid, "rst_sent": rst_sent, "rst_rcvd": rst_rcvd, "qsl_sent": qsl_sent, "qsl_rcvd": qsl_rcvd}.items() if v is not None}
    return await _call("PATCH", f"/api/qso/{qso_id}", headers=_auth(key), json=body)


class _Confirmacion(BaseModel):
    confirmar: bool = Field(description="Marca la casilla para confirmar el borrado.")


async def _confirmar_borrado(ctx: Context, qso_id: int, key: str | None = None):
    """Pregunta a la PERSONA (elicitación) antes de borrar. Con clientes del protocolo 2026-07-28 la pregunta viaja en la respuesta
    (`input_required`) y el cliente repite la llamada; con los anteriores, como petición del servidor en mitad de la llamada (lo hace el SDK 2).
    Si el cliente no sabe preguntar, se fía de su propia aprobación (la herramienta lleva destructiveHint)."""
    cap = ctx.client_capabilities
    el = cap.elicitation if cap is not None else None
    if el is None or (el.form is None and el.url is not None):
        return _Confirmacion(confirmar=True)
    que = f"el contacto {qso_id}"
    try:
        for q in await _call("GET", "/api/qso", headers=_auth(key), params={"limit": 1000}):
            if q.get("id") == qso_id:
                que = f"el contacto con {q['call']} del {str(q['qso_at'])[:16].replace('T', ' ')} UTC ({q.get('band') or '¿banda?'}, {q.get('mode') or '¿modo?'})"
                break
    except Exception:  # noqa: BLE001  (si no se puede leer, se pregunta igual con el número)
        pass
    return Elicit(f"Radio Guilda va a borrar del cuaderno {que}. No se puede deshacer. ¿Lo confirmas?", _Confirmacion)


_confirmar_borrado.__annotations__ = {"ctx": Context, "qso_id": int, "key": str | None}


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, read_only_hint=False))
async def logbook_delete(qso_id: int, key: str | None = None,
                         confirmacion: Annotated[ElicitationResult[_Confirmacion], Resolve(_confirmar_borrado)] = None) -> dict:
    """Borra un contacto del cuaderno (no se puede deshacer). Antes pide confirmación a la persona si el cliente lo admite."""
    datos = confirmacion.data if isinstance(confirmacion, AcceptedElicitation) else confirmacion
    if not isinstance(datos, _Confirmacion) or not datos.confirmar:
        raise ErrorLegible("No se ha borrado nada: la persona no lo ha confirmado.")
    await _call("DELETE", f"/api/qso/{qso_id}", headers=_auth(key))
    return {"borrado": qso_id}


@mcp.tool()
async def list_ai_models(ctx: Context, provider: Literal["server", "openrouter", "openai", "anthropic"]) -> dict:
    """Modelos que ofrece un proveedor de IA (con tu clave guardada, si el proveedor la usa). Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/me/models", params={"provider": provider})


@mcp.tool()
async def test_ai_connection(ctx: Context) -> dict:
    """Prueba tu configuración de IA (proveedor, modelo y clave) con una llamada mínima: {ok, modelo, ms} o {ok:false, error}. Requiere OAuth."""
    return await _as(_need_who(ctx), "POST", "/api/me/test")


# ---------- Alertas de radio y salidas, y rutas guardadas (de la cuenta: OAuth) ----------
@mcp.tool()
async def list_alerts(ctx: Context) -> dict:
    """Tus alertas de radio y salidas (tropo VHF/UHF, HF, pasos de satélite, tiempo en un lugar o en una ruta guardada), tus límites (tope de avisos al día y
    horario de silencio), lo último que se te envió, cuántos dispositivos las reciben y el catálogo de tipos y parámetros. Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/alertas")


@mcp.tool()
async def create_alert(ctx: Context, tipo: Literal["tropo", "hf", "satelite", "meteo", "ruta"], params: dict, nombre: str | None = None) -> dict:
    """Crea una alerta que te llega como notificación push a tus dispositivos (actívalas antes en la web, Ajustes → Avisos). `params` según `tipo`:
    tropo {lat, lon, nombre?, nivel: "super-refracción"|"conducto"} · hf {modo: "mejora"|"tormenta", sfi_min, kp_max, kp_min} ·
    satelite {norad: [ids, máx. 5], lat, lon, nombre?, min_el, antelacion_min} · meteo {lat, lon, nombre?, variable: viento|racha|lluvia|oleaje|aviso, umbral (aviso: nivel 1-3)} ·
    ruta {ruta_id (de list_saved_routes), variable, umbral}. Cada alerta avisa como mucho una vez al día (los satélites, una por paso). Requiere OAuth."""
    return await _as(_need_who(ctx), "POST", "/api/alertas", json={"tipo": tipo, "params": params, "nombre": nombre})


@mcp.tool()
async def update_alert(ctx: Context, alert_id: int, active: bool | None = None, name: str | None = None) -> dict:
    """Pausa (`active=false`), reanuda o renombra una alerta. Requiere OAuth."""
    return await _as(_need_who(ctx), "PATCH", f"/api/alertas/{alert_id}", json={"activa": active, "nombre": name})


@mcp.tool()
async def delete_alert(ctx: Context, alert_id: int) -> dict:
    """Borra una alerta. Requiere OAuth."""
    return await _as(_need_who(ctx), "DELETE", f"/api/alertas/{alert_id}")


@mcp.tool()
async def set_alert_limits(ctx: Context, max_per_day: int | None = None, quiet_from_hour: int | None = None, quiet_to_hour: int | None = None,
                           timezone: str | None = None, no_quiet_hours: bool = False) -> dict:
    """Tus límites de alertas: tope de avisos al día entre todas (1-30) y horario de silencio (horas 0-23 en tu zona, p. ej. 23 a 7; `no_quiet_hours=true` lo quita).
    Lo que cae en el silencio no se manda y se vuelve a mirar después. Requiere OAuth."""
    body = {"max_dia": max_per_day, "silencio_desde": quiet_from_hour, "silencio_hasta": quiet_to_hour, "zona": timezone, "sin_silencio": no_quiet_hours}
    return await _as(_need_who(ctx), "PUT", "/api/alertas/prefs", json=body)


@mcp.tool()
async def check_alert(ctx: Context, alert_id: int) -> dict:
    """Evalúa ahora una alerta SIN mandar nada: si saltaría, con qué texto, y si la frenarían el silencio o el tope diario. Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", f"/api/alertas/{alert_id}/comprobar")


@mcp.tool()
async def list_saved_routes(ctx: Context) -> dict:
    """Tus rutas guardadas (nombre, modo y puntos de paso): para las alertas de ruta y el planificador de salidas. Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/rutas")


@mcp.tool()
async def save_route(ctx: Context, name: str, waypoints: list[dict], mode: Literal["foot", "car", "sea"] = "foot") -> dict:
    """Guarda una ruta con nombre: `waypoints` = 2-10 puntos {lat, lon, nombre?}; `mode` foot (a pie/monte), car (carretera) o sea (navegación, en línea recta).
    Con un nombre que ya existe, la sustituye. Requiere OAuth."""
    return await _as(_need_who(ctx), "POST", "/api/rutas", json={"nombre": name, "modo": mode, "waypoints": waypoints})


@mcp.tool()
async def delete_saved_route(ctx: Context, route_id: int) -> dict:
    """Borra una ruta guardada y las alertas que dependían de ella. Requiere OAuth."""
    return await _as(_need_who(ctx), "DELETE", f"/api/rutas/{route_id}")


# ---------- Cuaderno conectado con LoTW, eQSL y QRZ (las credenciales solo se guardan en la web) ----------
@mcp.tool()
async def qsl_services_status(ctx: Context) -> dict:
    """Qué servicios de confirmación tienes conectados (LoTW, eQSL, QRZ), su usuario y su último resultado. Las credenciales NO se pueden ver ni
    guardar por aquí: se escriben solo en la web (Radio → Cuaderno → Servicios). Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/qsl/servicios")


@mcp.tool()
async def qsl_upload(ctx: Context, service: Literal["lotw", "eqsl", "qrz"], key: str | None = None) -> dict:
    """Sube al servicio (LoTW, eQSL o QRZ) los contactos del cuaderno que aún no se le han enviado (hasta 300 por llamada). Necesita el servicio conectado
    en la web y la clave del cuaderno (`key` o MG_LOGBOOK_KEY). Requiere OAuth."""
    return await _as(_need_who(ctx), "POST", f"/api/qsl/{service}/subir", headers=_auth(key))


@mcp.tool()
async def qsl_import_confirmations(ctx: Context, service: Literal["lotw", "eqsl", "qrz"], from_scratch: bool = False, key: str | None = None) -> dict:
    """Baja las confirmaciones (QSL recibidas) del servicio desde la última vez (o todas con `from_scratch`) y las marca en el cuaderno: {descargadas,
    confirmados_nuevos, sin_pareja}. Requiere OAuth y la clave del cuaderno."""
    return await _as(_need_who(ctx), "POST", f"/api/qsl/{service}/confirmaciones", headers=_auth(key), params={"desde_cero": from_scratch or None})


# ---------- Verificación de modelos («Quién acierta más») ----------
@mcp.tool()
async def model_accuracy(lat: float, lon: float, days: int = 30) -> dict:
    """Qué modelo ha acertado más la temperatura a 24 h en la zona (lugar extra verificado a ≤ 5 km o lugar principal a ≤ 60 km), en los últimos `days`
    días (7-90), frente a estaciones oficiales. `disponible=false` si aún no hay datos."""
    return await _call("GET", "/api/precision", params={"lat": lat, "lon": lon, "dias": days})


@mcp.tool()
async def model_verification_report(ctx: Context, month: str | None = None) -> dict:
    """Informe mensual «Quién acierta más» (`month` = AAAA-MM; por defecto el mes en curso): por cada lugar principal y cada lugar extra tuyo, ranking de
    modelos por error medio de la temperatura a 24 y 12 h, sesgo, % de aciertos a ±2 °C, el ganador y su error del mes anterior; y un ranking general.
    Solo lee lo guardado (no gasta consultas). Requiere OAuth."""
    return await _quiza_enlace(ctx, f"verificacion-{month or 'mes'}", await _as(_need_who(ctx), "GET", "/api/verificacion/informe", params={"mes": month}))


@mcp.tool()
async def get_my_verified_places(ctx: Context) -> dict:
    """Tus lugares extra (hasta 5) en los que se comprueba qué modelo acierta más, con cuántas comparaciones llevan. Requiere OAuth."""
    return await _as(_need_who(ctx), "GET", "/api/verificacion/mis-lugares")


@mcp.tool()
async def set_my_verified_places(ctx: Context, places: list[dict]) -> dict:
    """Sustituye tus lugares extra a verificar: lista de 0-5 {lat, lon, nombre?} (tus favoritos o tu ubicación). Se consultan una vez cada 6 h, solo si la cuota
    de Open-Meteo va holgada, y hacen falta estaciones oficiales a ≤ 25 km para comparar. Requiere OAuth."""
    return await _as(_need_who(ctx), "PUT", "/api/verificacion/mis-lugares", json={"lugares": places})


# ---------- Planificador de salidas por tramos ----------
@mcp.tool()
async def plan_outing(ctx: Context, kind: Literal["monte", "carretera", "mar"] | None = None, route_id: int | None = None, waypoints: list[dict] | None = None,
                      depart_iso: str | None = None, knots: float = 5) -> dict:
    """Planifica una salida por tramos: semáforo de riesgo por tramo (0 verde, 1 ámbar, 2 rojo) con sus motivos, tramos críticos, mejor ventana de salida
    en 36 h y equipo sugerido. `kind`: monte (a pie, con cota de nieve, sensación térmica, niebla y tormentas), carretera (hielo, niebla, viento lateral) o
    mar (línea recta a `knots` nudos, oleaje y viento). Usa `route_id` (ruta guardada; requiere OAuth) o `waypoints` [{lat, lon}…]. Reglas, no IA."""
    body = {"perfil": kind, "ruta_id": route_id, "waypoints": waypoints or [], "depart": depart_iso, "nudos": knots}
    if route_id is not None:
        res = await _as(_need_who(ctx), "POST", "/api/salidas", json=body)
    else:
        res = await _call("POST", "/api/salidas", json=body)
    res.pop("muestras", None)
    return await _quiza_enlace(ctx, "salida", res)


# ---------- APRS en vivo y clúster DX ----------
@mcp.tool()
async def get_aprs_stations(ctx: Context, lat: float | None = None, lon: float | None = None, radius_km: float = 50, south: float | None = None, west: float | None = None,
                            north: float | None = None, east: float | None = None, max_age_min: int = 60) -> Any:
    """Estaciones APRS recientes (última posición de cada una: tipo, velocidad, rumbo, altitud, comentario, hace cuántos minutos) cerca de un punto (lat, lon,
    radius_km) o en un recuadro (south, west, north, east). Cubre 60 km alrededor de los lugares principales."""
    p = {"max_age_min": max_age_min}
    if lat is not None and lon is not None:
        p.update(lat=lat, lon=lon, km=radius_km)
    elif None not in (south, west, north, east):
        p["bbox"] = f"{south},{west},{north},{east}"
    return await _quiza_enlace(ctx, "aprs", await _call("GET", "/api/aprs", params=p))


@mcp.tool()
async def get_dx_spots(band: str | None = None, mode: str | None = None, continent: str | None = None, search: str | None = None, limit: int = 30) -> dict:
    """Spots recientes del clúster DX (fuente pública, caché de 2 min): indicativo DX, frecuencia, banda, modo, comentario, hora UTC, país y quién lo anunció.
    Filtros: band (20m, 40m…), mode (FT8, CW, SSB…), continent (EU, NA, SA, AS, AF, OC, AN), search (indicativo o país)."""
    return await _call("GET", "/api/dxcluster", params={"banda": band, "modo": mode, "continente": continent, "buscar": search, "limite": limit})


class _Bearer:
    """Middleware ASGI: exige `Authorization: Bearer …`. Vale la clave fija MG_MCP_TOKEN (opcional) o un token OAuth emitido por
    /api/oauth/token, que se valida contra /api/oauth/introspect de la API (con caché de 60 s)."""

    def __init__(self, app, token: str | None = None, introspect=None, resource_metadata: str | None = None):
        self.app, self.token, self.introspect, self.resource_metadata = app, token, introspect, resource_metadata
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def _check(self, bearer: str) -> tuple[bool, dict | None]:
        """(¿válido?, identidad). La clave fija vale pero no identifica a nadie; un token OAuth trae {sub, email}."""
        if not bearer:
            return False, None
        if self.token and hmac.compare_digest(bearer, self.token):
            return True, None
        if not self.introspect:
            return False, None
        now = time.monotonic()
        hit = self._cache.get(bearer)
        if hit and hit[0] > now:
            return True, hit[1]
        who = self.introspect(bearer)           # dict {sub, email} o None
        if who:
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[bearer] = (now + 60, who)
            return True, who
        return False, None

    def _valid(self, bearer: str) -> bool:
        return self._check(bearer)[0]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            bearer = auth[7:] if auth.startswith("Bearer ") else ""
            ok, who = await asyncio.to_thread(self._check, bearer)
            # cabeceras internas con la identidad verificada (las que mande el cliente se descartan: no se pueden falsificar)
            scope["headers"] = [(k, v) for k, v in scope["headers"] if not k.lower().startswith(b"x-mg-")]
            if ok and who:
                scope["headers"] += [(b"x-mg-sub", who["sub"].encode()), (b"x-mg-email", (who.get("email") or "").encode())]
            if not ok:
                headers = [(b"content-type", b"text/plain")]
                if self.resource_metadata:
                    headers.append((b"www-authenticate", f'Bearer resource_metadata="{self.resource_metadata}"'.encode()))
                await send({"type": "http.response.start", "status": 401, "headers": headers})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def _introspector():
    """Función que pregunta a la API si un token OAuth es válido; None si el OAuth no está configurado."""
    if not os.environ.get("MG_OIDC_ISSUER") or not os.environ.get("MG_ADMIN_TOKEN"):
        return None
    api, admin = os.environ.get("MG_API", "http://127.0.0.1:8000"), os.environ["MG_ADMIN_TOKEN"]

    def check(bearer: str) -> dict | None:
        try:
            r = httpx.post(f"{api}/api/oauth/introspect", data={"token": bearer}, headers={"X-Admin-Token": admin}, timeout=5)
            d = r.json() if r.status_code == 200 else {}
            return {"sub": d["sub"], "email": d.get("email") or ""} if d.get("active") and d.get("sub") else None
        except Exception:  # noqa: BLE001
            return None
    return check


def http_app():
    token = os.environ.get("MG_MCP_TOKEN") or None
    introspect = _introspector()
    if not token and not introspect:
        raise SystemExit("Define MG_OIDC_ISSUER (OAuth) o MG_MCP_TOKEN para exponer el MCP por HTTP")
    app = mcp.streamable_http_app(host="0.0.0.0", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    public = os.environ.get("MG_PUBLIC_URL", "https://radio.guildawork.com").rstrip("/")
    return _Bearer(app, token, introspect, f"{public}/.well-known/oauth-protected-resource/mcp" if introspect else None)


if __name__ == "__main__":
    if "--http" in sys.argv:
        import uvicorn
        uvicorn.run(http_app(), host="0.0.0.0", port=int(os.environ.get("MG_MCP_PORT", "8001")), log_level="warning")
    else:
        mcp.run("stdio")
