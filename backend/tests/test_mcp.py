import asyncio

import httpx
import pytest

from meteoguilda import main, mcp_server
from tests.test_api import api  # noqa: F401  (fixtures con API simulada)


async def test_mcp_tools(api, monkeypatch):  # noqa: F811
    tools = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"list_places", "search_place", "get_forecast_data", "get_ai_forecast", "plan_route", "get_nearby_observations",
            "get_vhf_propagation", "get_hf_conditions", "find_repeaters", "maidenhead", "get_forecast_by_name"} <= tools

    async with main.app.router.lifespan_context(main.app):
        transport = httpx.ASGITransport(app=main.app)
        monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=transport, base_url="http://t"))
        res = await mcp_server.mcp.call_tool("maidenhead", {"lat": 48.14666, "lon": 11.60833})
        assert "JN58td" in str(res)
        res = await mcp_server.mcp.call_tool("get_forecast_data", {"lat": 42.7, "lon": 0.8})
        assert "tramos" in str(res)
        res = await mcp_server.mcp.call_tool("get_forecast_by_name", {"place_name": "Vielha"})
        assert "tramos" in str(res) and "Vielha" in str(res)
        res = await mcp_server.mcp.call_tool("plan_route", {"waypoints": [{"lat": 42.7, "lon": 0.8}, {"lat": 42.75, "lon": 0.9}]})
        assert "ventanas" in str(res) and "geometria" not in str(res)


def test_bearer_middleware():
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    inner = Starlette(routes=[Route("/", lambda r: PlainTextResponse("ok"))])
    c = TestClient(mcp_server._Bearer(inner, "s3cret"))
    assert c.get("/").status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer s3cret"}).text == "ok"
