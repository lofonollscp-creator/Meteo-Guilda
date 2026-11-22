"""Servidor MCP de Meteo Guilda: expone los datos a modelos de IA (Claude, GPT, etc.).

Es un cliente fino de la API REST (mismas cachés y límites). Transportes:
  python -m meteoguilda.mcp_server            -> stdio (Claude Desktop / Claude Code local)
  python -m meteoguilda.mcp_server --http     -> HTTP streamable en :8001/mcp (token con MG_MCP_TOKEN)
"""
from __future__ import annotations

import hmac
import os
import sys
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

API = os.environ.get("MG_API", "http://127.0.0.1:8000")

mcp = MCPServer(
    "Meteo Guilda",
    instructions=(
        "Datos meteorológicos SIN cocinar: media de modelos numéricos abiertos (ECMWF, ICON, GFS, Météo-France) y "
        "balizas APRS reales con control de calidad. Usa get_forecast_data para razonar tú mismo con los datos; "
        "get_ai_forecast devuelve además la lectura de un modelo gratuito. Cita siempre la confianza, no inventes "
        "cifras y recuerda consultar los avisos oficiales (AEMET, Meteocat, Météo-France) en actividades de riesgo. "
        "Los niveles de riesgo de plan_route son 0 favorable, 1 precaución, 2 desaconsejable."),
)
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=API, timeout=60, headers={"User-Agent": "MeteoGuilda-MCP/0.2"})
    return _client


async def _call(method: str, path: str, **kw) -> Any:
    r = await client().request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"API {r.status_code}: {detail}")
    return r.json()


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


class _Bearer:
    """Middleware ASGI: exige `Authorization: Bearer <MG_MCP_TOKEN>`."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not hmac.compare_digest(auth, f"Bearer {self.token}"):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def http_app():
    token = os.environ.get("MG_MCP_TOKEN")
    if not token:
        raise SystemExit("Define MG_MCP_TOKEN para exponer el MCP por HTTP")
    app = mcp.streamable_http_app(host="0.0.0.0", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    return _Bearer(app, token)


if __name__ == "__main__":
    if "--http" in sys.argv:
        import uvicorn
        uvicorn.run(http_app(), host="0.0.0.0", port=int(os.environ.get("MG_MCP_PORT", "8001")), log_level="warning")
    else:
        mcp.run("stdio")
