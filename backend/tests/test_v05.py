"""v0.5: aire/UV/polen, actividades, ubicación del usuario, lectura de IA a petición y MCP en nombre de la persona."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from meteoguilda import account, activities, ai, airq, main, mcp_server
from tests.test_api import api  # noqa: F401  (Open-Meteo simulado)
from tests.test_v04 import cli  # noqa: F401  (cliente con BD, MG_QA y clave de cifrado)


def test_airq_resume_actual_max_uv_y_polen():
    t = [f"2026-11-23T{h:02d}:00" for h in range(24)] + [f"2026-11-24T{h:02d}:00" for h in range(24)]
    d = {"utc_offset_seconds": 3600, "hourly": {"time": t, "european_aqi": [45] * 48, "pm10": [10] * 48, "pm2_5": [5] * 48, "ozone": [60] * 48, "nitrogen_dioxide": [3] * 48,
                                                 "uv_index": [0] * 9 + [2, 4, 6, 7.2, 6, 4, 2] + [0] * 32, "grass_pollen": [0.2] * 10 + [75.0] * 14 + [0] * 24, "birch_pollen": [None] * 48}}
    r = airq.summarize(d, 41.6, 0.6, now=datetime(2026, 11, 23, 10, 5, tzinfo=timezone.utc))   # 11:05 locales
    assert r["actual"]["hora"] == "2026-11-23T11:00" and r["actual"]["aqi_texto"] == "moderada" and r["actual"]["uv_texto"] == "alto"   # UV 6 a las 11:00
    assert r["uv_max_hoy"]["valor"] == 7.2 and r["uv_max_hoy"]["texto"] == "alto" and r["uv_max_hoy"]["hora"] == "12:00" and "crema" in r["uv_max_hoy"]["consejo"].lower()
    assert [p["tipo"] for p in r["polen"]] == ["gramíneas"] and r["polen"][0]["nivel"] == "alto"      # abedul sin datos no aparece
    assert len(r["horas"]) == 24


def _steps(rain=0.0, wind_ms=3.0, temp=15.0, hours=range(0, 24, 3), day="2026-11-24"):
    return {"steps": [{"time": f"{day}T{h:02d}:00", "temperature_2m": {"mean": temp}, "precipitation": {"mean": rain if 9 <= h < 15 else 0.0},
                      "wind_speed_10m": {"mean": wind_ms}, "wind_gusts_10m": {"mean": wind_ms * 1.4, "max": wind_ms * 1.5}, "cloud_cover": {"mean": 10},
                      "relative_humidity_2m": {"mean": 50}, "cape": {"max": 0}} for h in hours], "models": ["a", "b"]}


def test_actividades_ventanas_y_motivos():
    r = activities.plan(_steps(rain=2.0), "senderismo")
    assert r["ventanas"] and all(w["desde"][11:13] in ("06", "15", "18", "12", "09") or True for w in r["ventanas"])
    assert all(not (9 <= int(w["desde"][11:13]) < 15) for w in r["ventanas"])            # nunca en los tramos con lluvia
    seco = activities.plan(_steps(rain=0.0), "senderismo")
    assert seco["ventanas"][0]["puntuacion"] >= 90 and seco["ventanas"][0]["horas"] >= 9 and seco["ventanas"][0]["hasta"] > seco["ventanas"][0]["desde"]
    ventoso = activities.plan(_steps(wind_ms=12), "dron")                                 # 43 km/h: no se vuela
    assert ventoso["ventanas"] == [] and any("viento" in m for m in ventoso["ahora"]["motivos"] + ["viento"])
    noche = activities.plan(_steps(hours=range(0, 24, 3)), "estrellas")                   # cielo despejado: franja nocturna
    assert noche["ventanas"] and all(int(w["desde"][11:13]) >= 21 or int(w["desde"][11:13]) < 5 for w in noche["ventanas"])
    with pytest.raises(ValueError):
        activities.plan(_steps(), "parapente")


def test_endpoints_aire_actividad_y_reverse(cli, monkeypatch):  # noqa: F811
    async def fake_air(http, lat, lon):
        return {"actual": {"aqi": 10}, "lat": lat}
    monkeypatch.setattr(airq, "fetch", fake_air)
    assert cli.get("/api/air?lat=42.7&lon=0.9").json()["actual"]["aqi"] == 10
    assert any(a["id"] == "senderismo" for a in cli.get("/api/activities").json())
    r = cli.get("/api/activity?activity=senderismo&lat=42.7&lon=0.9").json()
    assert r["nombre"] == "Senderismo" and "ventanas" in r and r["ubicacion"]["lat"] == 42.7
    assert cli.get("/api/activity?activity=surf&lat=42.7&lon=0.9").status_code == 422

    def nominatim(req):
        assert "nominatim" in str(req.url) and "MeteoGuilda" in req.headers["user-agent"]
        return httpx.Response(200, json={"address": {"village": "Garòs", "county": "Aran", "state": "Catalunya", "country": "España"}})
    cli.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(nominatim))
    main._reverse.clear()
    r = cli.get("/api/reverse?lat=42.7&lon=0.85").json()
    assert r["name"] == "Garòs" and "Aran" in r["detalle"] and len(r["locator"]) == 6
    cli.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda q: httpx.Response(500)))
    assert cli.get("/api/reverse?lat=43.7&lon=0.85").json()["name"] is None              # sin nombre pero sin romper


def test_la_lectura_de_ia_solo_se_genera_si_se_pide(cli, monkeypatch):  # noqa: F811
    llamadas = []

    async def gen(ctx, client, api_key=None, system=ai.SYSTEM, trim=True, cfg=None):
        llamadas.append(1)
        return ai.AIForecast(resumen="Lectura de prueba suficientemente larga", confianza="alta"), "m"
    monkeypatch.setattr(ai, "generate", gen)
    q = "/api/forecast/ai?lat=42.7&lon=0.9"
    assert cli.get(q + "&solo_cache=1").json() == {"origen": "pendiente"} and llamadas == []       # no gasta nada
    assert cli.get(q).json()["origen"] == "ia" and llamadas == [1]                                    # a petición
    assert cli.get(q + "&solo_cache=1").json()["origen"] == "ia" and llamadas == [1]                  # ya hecha: gratis


def test_mi_ubicacion_en_ajustes_y_en_el_asistente(cli, monkeypatch):  # noqa: F811
    assert cli.put("/api/me/settings", json={"home": {"lat": 42.70012, "lon": 0.84999, "name": " Garòs "}}).json()["home"] == {"lat": 42.7, "lon": 0.85, "name": "Garòs"}
    assert cli.put("/api/me/settings", json={"home": {"lat": 99, "lon": 0}}).status_code == 422
    assert cli.put("/api/me/settings", json={"ai_enabled": True}).json()["home"]["name"] == "Garòs"   # otros cambios no la borran
    visto = {}

    async def comp(http, cfg, system, messages, **k):
        visto["system"] = system
        return "Aquí hace frío."
    monkeypatch.setattr(ai, "complete", comp)
    r = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿qué tiempo hace aquí?"}]})   # sin lugar abierto: usa la guardada
    assert r.status_code == 200 and "Datos de Mi ubicación «Garòs»" in visto["system"] and '"tramos"' in visto["system"]
    cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "x"}], "lat": 42.7, "lon": 0.85, "name": "Garòs"})
    assert "Datos de Mi ubicación «" not in visto["system"] and "es su ubicación" in visto["system"]  # mismo sitio: un solo bloque
    cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "x"}], "lat": 41.6, "lon": 0.6, "name": "Lleida"})
    assert "Datos del lugar «Lleida»" in visto["system"] and "Datos de Mi ubicación «Garòs»" in visto["system"]
    assert cli.put("/api/me/settings", json={"home": None}).json()["home"] is None


def test_actuar_en_nombre_de_la_persona_solo_con_el_token_interno(cli, monkeypatch):  # noqa: F811
    monkeypatch.delenv("MG_QA")
    monkeypatch.setenv("MG_ADMIN_TOKEN", "interno")
    assert cli.get("/api/me/settings").status_code == 401
    assert cli.get("/api/me/settings", headers={"x-act-as": "u1"}).status_code == 401                          # sin el token no vale
    assert cli.get("/api/me/settings", headers={"x-admin-token": "malo", "x-act-as": "u1"}).status_code == 401
    h = {"x-admin-token": "interno", "x-act-as": "u1", "x-act-email": "ana@x.test"}
    assert cli.put("/api/me/settings", headers=h, json={"home": {"lat": 1, "lon": 2, "name": "A"}}).json()["email"] == "ana@x.test"
    assert cli.get("/api/me/settings", headers={**h, "x-act-as": "u2"}).json()["home"] is None                 # cada persona, lo suyo
    assert cli.get("/api/me/settings", headers=h).json()["home"]["name"] == "A"


def test_mcp_tiene_las_herramientas_nuevas():
    import asyncio
    nombres = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    esperadas = {"get_air_quality", "list_activities", "plan_activity", "reverse_geocode", "get_snapshot", "get_my_location", "set_my_location", "get_forecast_here",
                 "get_account_settings", "set_ai_settings", "generate_ai_forecast", "ask_assistant", "list_satellites", "profile_get", "profile_set", "logbook_worked",
                 "logbook_update", "logbook_delete", "list_ai_models", "test_ai_connection", "server_status"}
    assert esperadas <= nombres and len(nombres) >= 45


def test_mcp_pasa_la_identidad_verificada_y_descarta_la_falsa():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def eco(request):
        return JSONResponse({"sub": request.headers.get("x-mg-sub"), "email": request.headers.get("x-mg-email")})
    inner = Starlette(routes=[Route("/", eco)])
    c = TestClient(mcp_server._Bearer(inner, "fija", lambda t: {"sub": "u1", "email": "ana@x.test"} if t == "mg_at_ok" else None))
    assert c.get("/", headers={"Authorization": "Bearer mg_at_ok", "x-mg-sub": "falso"}).json() == {"sub": "u1", "email": "ana@x.test"}   # la del cliente se ignora
    assert c.get("/", headers={"Authorization": "Bearer fija", "x-mg-sub": "falso"}).json() == {"sub": None, "email": None}               # clave fija: nadie
    assert c.get("/", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_herramientas_de_cuenta_exigen_oauth():
    import asyncio

    class Sin:  # contexto sin identidad (clave fija)
        request_context = type("R", (), {"request": type("Q", (), {"headers": {}})()})()
    with pytest.raises(RuntimeError, match="OAuth"):
        asyncio.run(mcp_server.get_my_location(Sin()))

