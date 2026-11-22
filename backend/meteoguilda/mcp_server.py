"""Servidor MCP de Meteo Guilda: expone los datos a modelos de IA (Claude, GPT, etc.).

Es un cliente fino de la API REST (mismas cachés y límites). Transportes:
  python -m meteoguilda.mcp_server            -> stdio (Claude Desktop / Claude Code local)
  python -m meteoguilda.mcp_server --http     -> HTTP streamable en :8001/mcp (token con MG_MCP_TOKEN)
"""
from __future__ import annotations

import asyncio
import hmac
import os
import sys
import time
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

API = os.environ.get("MG_API", "http://127.0.0.1:8000")

mcp = MCPServer(
    "Meteo Guilda / Radio Guilda",
    instructions=(
        "Datos meteorológicos SIN cocinar: media de modelos numéricos abiertos (ECMWF, ICON, GFS, Météo-France) y "
        "balizas APRS reales con control de calidad. Usa get_forecast_data para razonar tú mismo con los datos; "
        "get_ai_forecast devuelve además la lectura de un modelo gratuito. Cita siempre la confianza, no inventes "
        "cifras y recuerda consultar los avisos oficiales (AEMET, Meteocat, Météo-France) en actividades de riesgo. "
        "Los niveles de riesgo de plan_route son 0 favorable, 1 precaución, 2 desaconsejable. Radio Guilda añade repetidores "
        "(BrandMeister y listados abiertos), propagación, satélites, ventanas para operar en portátil y un cuaderno de contactos "
        "(QSO/QSL) que requiere la clave del cuaderno (parámetro key o variable MG_LOGBOOK_KEY)."),
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
        raise RuntimeError("Falta la clave del cuaderno: pásala en `key` o define MG_LOGBOOK_KEY")
    return {"Authorization": f"Bearer {k}"}


async def _call(method: str, path: str, **kw) -> Any:
    if kw.get("params"):
        kw["params"] = {k: v for k, v in kw["params"].items() if v is not None}
    r = await client().request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"API {r.status_code}: {detail}")
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return {"texto": r.text}


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
async def get_forecast_data(lat: float, lon: float) -> dict:
    """Datos previstos crudos en tramos de 3 h hasta 5 días: temperatura, lluvia, nieve, viento y rachas (km/h),
    dirección, nubes, humedad, presión, CAPE, isocero, con rango entre modelos y confianza, más balizas APRS
    cercanas. Sin texto de IA: interprétalos tú."""
    return await _call("GET", "/api/raw", params={"lat": lat, "lon": lon})


@mcp.tool()
async def get_ai_forecast(place: str | None = None, lat: float | None = None, lon: float | None = None) -> dict:
    """Pronóstico ya redactado por la IA de Meteo Guilda (resumen, riesgos, ventanas, recomendaciones) más los
    datos de origen. Pasa `place` (slug: garos, premia-de-mar, tortoles-de-esgueva, bresca, la-garrovilla,
    bagneres-de-bigorre) o lat+lon."""
    params = {"place": place} if place else {"lat": lat, "lon": lon}
    return await _call("GET", "/api/forecast", params=params)


@mcp.tool()
async def get_nearby_observations(lat: float, lon: float, radius_km: float = 40) -> list[dict]:
    """Observaciones reales recientes de estaciones meteo APRS/CWOP cercanas (con control de calidad)."""
    return await _call("GET", "/api/observations", params={"lat": lat, "lon": lon, "km": radius_km})


@mcp.tool()
async def plan_route(waypoints: list[dict], mode: Literal["foot", "car"] = "foot", depart_iso: str | None = None,
                     with_ai_reading: bool = False) -> dict:
    """Planifica una ruta a pie o en coche y evalúa la meteo a la hora de paso en cada punto.
    `waypoints`: lista de {"lat":…, "lon":…} (mínimo 2). `depart_iso`: salida en ISO 8601 con zona (máx. 72 h).
    Devuelve distancia, duración, desnivel, nivel de riesgo (0-2) por tramo con motivos, mejores ventanas
    de salida, balizas y repetidores cercanos al recorrido."""
    body = {"waypoints": waypoints, "mode": mode, "depart": depart_iso, "ia": with_ai_reading}
    res = await _call("POST", "/api/route", json=body)
    res.pop("geometria", None)  # pesada; el modelo no la necesita
    return res


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
                           mode: Literal["FM", "DMR", "D-STAR", "C4FM"] | None = None, band: str | None = None, limit: int = 40) -> list[dict]:
    """Busca repetidores: por texto (indicativo o ciudad) o por cercanía (lat+lon+radius_km). Filtra por modo (FM, DMR, D-STAR,
    C4FM) y banda (2m, 70cm...). Datos de BrandMeister y listados abiertos (HearHam); incluye color code, subtono y desplazamiento."""
    if lat is not None and lon is not None:
        return await _call("GET", "/api/repeaters/near", params={"lat": lat, "lon": lon, "km": radius_km, "mode": mode, "band": band, "limit": limit})
    return await _call("GET", "/api/repeaters", params={"q": query, "mode": mode, "band": band, "limit": limit})


@mcp.tool()
async def get_repeater(repeater_id: int, compute_coverage: bool = False) -> dict:
    """Ficha completa de un repetidor. Con compute_coverage=True calcula y guarda su cobertura estimada por línea de vista
    (tarda unos segundos) y devuelve el polígono GeoJSON."""
    if compute_coverage:
        return await _call("POST", f"/api/repeaters/{repeater_id}/coverage")
    return await _call("GET", f"/api/repeaters/{repeater_id}")


@mcp.tool()
async def repeater_stats() -> dict:
    """Resumen del catálogo de repetidores: total, por modo y por banda, fuentes y fecha de actualización."""
    return await _call("GET", "/api/repeaters/stats")


@mcp.tool()
async def satellite_passes(lat: float, lon: float, hours: float = 24, min_elevation: float = 10, norad_ids: list[int] | None = None) -> dict:
    """Próximos pasos de satélites de aficionados (ISS, SO-50, RS-44, AO-7...) sobre un punto: AOS/LOS (UTC), elevación máxima,
    azimuts y traza para dibujar el cielo. Sin norad_ids usa los más habituales."""
    params = {"lat": lat, "lon": lon, "hours": hours, "min_el": min_elevation}
    if norad_ids:
        params["sats"] = ",".join(map(str, norad_ids))
    return await _call("GET", "/api/satellites/passes", params=params)


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
async def get_stations(south: float, west: float, north: float, east: float, max_age_min: int = 120) -> list[dict]:
    """Estaciones meteo APRS en un recuadro (con calidad: ok/range/outlier)."""
    return await _call("GET", "/api/stations", params={"south": south, "west": west, "north": north, "east": east, "max_age_min": max_age_min})


@mcp.tool()
async def logbook_create_profile() -> dict:
    """Crea un cuaderno de contactos anónimo y devuelve su clave secreta (guárdala; solo se muestra una vez)."""
    return await _call("POST", "/api/profile")


@mcp.tool()
async def logbook_list(key: str | None = None, query: str | None = None, band: str | None = None, mode: str | None = None,
                       status: Literal["pending", "confirmed"] | None = None, limit: int = 50) -> list[dict]:
    """Lista contactos del cuaderno (QSO), con filtros. `status`: pending = sin QSL recibida, confirmed = con QSL recibida."""
    return await _call("GET", "/api/qso", headers=_auth(key), params={"q": query, "band": band, "mode": mode, "status": status, "limit": limit})


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
async def logbook_export_adif(key: str | None = None) -> dict:
    """Exporta el cuaderno en formato ADIF (texto)."""
    return await _call("GET", "/api/qso/adif", headers=_auth(key))


@mcp.tool()
async def logbook_import_adif(adif_text: str, key: str | None = None) -> dict:
    """Importa contactos desde el texto de un fichero ADIF."""
    return await _call("POST", "/api/qso/import", headers={**_auth(key), "content-type": "text/plain"}, content=adif_text.encode())


class _Bearer:
    """Middleware ASGI: exige `Authorization: Bearer …`. Vale la clave fija MG_MCP_TOKEN (opcional) o un token OAuth emitido por
    /api/oauth/token, que se valida contra /api/oauth/introspect de la API (con caché de 60 s)."""

    def __init__(self, app, token: str | None = None, introspect=None, resource_metadata: str | None = None):
        self.app, self.token, self.introspect, self.resource_metadata = app, token, introspect, resource_metadata
        self._cache: dict[str, float] = {}

    def _valid(self, bearer: str) -> bool:
        if not bearer:
            return False
        if self.token and hmac.compare_digest(bearer, self.token):
            return True
        if not self.introspect:
            return False
        now = time.monotonic()
        if self._cache.get(bearer, 0) > now:
            return True
        if self.introspect(bearer):
            if len(self._cache) > 500:
                self._cache = {k: v for k, v in self._cache.items() if v > now}
            self._cache[bearer] = now + 60
            return True
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            bearer = auth[7:] if auth.startswith("Bearer ") else ""
            if not await asyncio.to_thread(self._valid, bearer):
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

    def check(bearer: str) -> bool:
        try:
            r = httpx.post(f"{api}/api/oauth/introspect", data={"token": bearer}, headers={"X-Admin-Token": admin}, timeout=5)
            return r.status_code == 200 and bool(r.json().get("active"))
        except Exception:  # noqa: BLE001
            return False
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
