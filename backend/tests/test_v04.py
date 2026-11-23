"""v0.4: detalle por modelo, proveedores de IA, fuentes METAR/XEMA, previsión sin esperar a la IA y ajustes de la cuenta."""
import asyncio
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from meteoguilda import account, ai, main, openmeteo, sources
from tests.test_ai import fake_openmeteo
from tests.test_api import api  # noqa: F401  (fixture con Open-Meteo simulado)

FERNET = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="


def test_summarize_guarda_cada_modelo_para_contrastar():
    s = openmeteo.summarize(fake_openmeteo(), now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc))
    pm = s["por_modelo"]["temperature_2m"]
    assert set(pm) == {"ecmwf_ifs025", "icon_seamless", "gfs_seamless"} and pm["icon_seamless"][0] == 11.0
    assert s["models"] == ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]        # solo los que respondieron
    ctx = ai.build_context(s, [])
    assert ctx["por_modelo"]["temp_c"]["icon_seamless"][0] == 11.0
    assert ctx["por_modelo"]["viento_kmh"]["icon_seamless"][0] == round(9 * 3.6)  # m/s → km/h
    assert "por_modelo" not in ai.ai_view(ctx)                                     # la IA no recibe el detalle (ni se valida contra él)


def test_modelo_regional_sin_datos_no_cuenta():
    d = fake_openmeteo()
    d["hourly"]["temperature_2m_knmi_seamless"] = [None] * 24
    s = openmeteo.summarize(d, now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc))
    assert "knmi_seamless" not in s["por_modelo"]["temperature_2m"] and "knmi_seamless" not in s["models"]


def _mock(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_complete_habla_con_cada_proveedor():
    visto = {}

    def h(req: httpx.Request):
        body = json.loads(req.content)
        visto[str(req.url.host)] = (req.headers, body)
        if req.url.host == "api.anthropic.com":
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hola desde claude"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola desde " + req.url.host}}]})

    async with _mock(h) as c:
        assert await ai.complete(c, ai.AIConfig("anthropic", "claude-x", "k-ant"), "sys", [{"role": "user", "content": "hi"}]) == "hola desde claude"
        assert await ai.complete(c, ai.AIConfig("openai", "gpt-x", "k-oa"), "sys", [{"role": "user", "content": "hi"}]) == "hola desde api.openai.com"
        assert await ai.complete(c, ai.AIConfig("openrouter", "m/x", "k-or"), "sys", [{"role": "user", "content": "hi"}]) == "hola desde openrouter.ai"
    hd, body = visto["api.anthropic.com"]
    assert hd["x-api-key"] == "k-ant" and hd["anthropic-version"] and body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hi"}]
    assert visto["api.openai.com"][0]["authorization"] == "Bearer k-oa" and visto["api.openai.com"][1]["messages"][0] == {"role": "system", "content": "sys"}
    with pytest.raises(ValueError):
        async with _mock(h) as c:
            await ai.complete(c, ai.AIConfig("openai", "", "k"), "s", [])             # sin modelo


def test_friendly_error_no_filtra_urls():
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages?secret=1")
    for code, frag in [(401, "clave"), (429, "limita"), (404, "modelo"), (500, "500")]:
        msg = ai.friendly_error(httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req)))
        assert frag in msg and "http" not in msg


async def test_metar_y_xema():
    def h(req: httpx.Request):
        if req.url.host == "aviationweather.gov":
            return httpx.Response(200, json=[{"icaoId": "LEDA", "name": "Lleida-Alguaire Arpt, CT, ES", "temp": 9, "dewp": 4, "wdir": 260, "wspd": 12,
                                              "wgst": 20, "altim": 1024, "lat": 41.73, "lon": 0.54, "reportTime": "2026-11-23T00:00:00.000Z"}])
        if req.url.path.endswith("yqwd-vj5e.json"):
            return httpx.Response(200, json=[{"codi_estacio": "X1", "nom_estacio": "Lleida", "latitud": "41.63", "longitud": "0.60"}])
        rows = [{"codi_estacio": "X1", "codi_variable": v, "data_lectura": "2026-11-22T23:30:00.000", "valor_lectura": val}
                for v, val in [("32", "10.2"), ("30", "2.5"), ("50", "5"), ("33", "81"), ("34", "990.1")]]
        return httpx.Response(200, json=rows)

    sources._cache.clear(); sources._xema_meta = (0, [])
    async with _mock(h) as c:
        r = await sources.nearby(c, 41.62, 0.62, 40)
    por = {o["fuente"]: o for o in r}
    assert por["METAR"]["viento_kmh"] == round(12 * 1.852) and por["METAR"]["racha_kmh"] == round(20 * 1.852)
    assert por["METAR"]["id"] == "LEDA" and por["METAR"]["nombre"].startswith("Lleida")
    x = por["XEMA"]
    assert x["temp_c"] == 10.2 and x["viento_kmh"] == 9 and x["racha_kmh"] == 18 and x["humedad_pct"] == 81 and x["presion_hpa"] == 990.1 and x["hora"].endswith("Z")
    assert [o["dist_km"] for o in r] == sorted(o["dist_km"] for o in r)


async def test_fuentes_nunca_lanzan():
    def h(req):
        return httpx.Response(500)
    sources._cache.clear(); sources._xema_meta = (0, [])
    async with _mock(h) as c:
        assert await sources.nearby(c, 41.62, 0.62, 40) == []
        assert await sources.xema_near(c, 50.0, 8.0) == []                       # fuera de Cataluña: ni se consulta


@pytest.fixture
def cli(api, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MG_QA", "1")
    monkeypatch.setenv("MG_SECRET_KEY", FERNET)
    monkeypatch.setenv("OPENROUTER_API_KEY", "server-key")
    from meteoguilda import db
    with db.pool().connection() as con:
        con.execute("TRUNCATE user_settings")
    with TestClient(main.app) as c:
        yield c


def test_la_previsión_no_espera_a_la_ia(cli, monkeypatch):
    async def lenta(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(ai, "generate", lenta)
    t0 = time.time()
    r = cli.get("/api/forecast?lat=42.7&lon=0.9")
    assert r.status_code == 200 and time.time() - t0 < 5
    d = r.json()
    assert d["origen"] == "reglas" and d["datos"]["por_modelo"] and d["pronostico"]["resumen"]


def test_ia_aparte_con_cache_y_apagable(cli, monkeypatch):
    llamadas = []

    async def gen(ctx, client, api_key=None, system=ai.SYSTEM, trim=True, cfg=None):
        llamadas.append(cfg.provider)
        return ai.AIForecast(resumen="Lectura de prueba suficientemente larga", confianza="alta"), "modelo-x"
    monkeypatch.setattr(ai, "generate", gen)
    r = cli.get("/api/forecast/ai?lat=42.7&lon=0.9").json()
    assert r["origen"] == "ia" and r["modelo_ia"] == "modelo-x" and r["pronostico"]["resumen"].startswith("Lectura")
    cli.get("/api/forecast/ai?lat=42.7&lon=0.9")
    assert llamadas == ["server"]                                                     # segunda vez, de caché
    assert cli.put("/api/me/settings", json={"ai_enabled": False}).json()["ai_enabled"] is False
    assert cli.get("/api/forecast/ai?lat=42.7&lon=0.9").json() == {"origen": "desactivada"}
    assert cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "hola"}]}).status_code == 403


def test_si_la_ia_falla_se_devuelve_el_resumen_y_se_recuerda_poco(cli, monkeypatch):
    async def nada(*a, **k):
        return None
    monkeypatch.setattr(ai, "generate", nada)
    r = cli.get("/api/forecast/ai?lat=43.1&lon=0.3").json()
    assert r["origen"] == "reglas" and r["pronostico"]["resumen"]
    k = next(iter(main._ai))
    assert time.time() - main._ai[k][0] > main.AI_TTL - 400                           # caduca en ~5 min, no en 3 h


def test_ajustes_y_claves_cifradas(cli):
    from meteoguilda import db
    s = cli.get("/api/me/settings").json()
    assert s["ai_enabled"] is True and s["provider"] == "server" and s["can_store_keys"] and s["server_ai"] and "anthropic" in s["providers"]
    assert cli.put("/api/me/settings", json={"provider": "nope"}).status_code == 422
    assert cli.put("/api/me/key", json={"provider": "server", "key": "12345678"}).status_code == 422
    assert cli.put("/api/me/key", json={"provider": "anthropic", "key": "sk-ant-secreta-1234"}).json()["keys"]["anthropic"] == {"set": True, "last4": "1234"}
    cli.put("/api/me/settings", json={"provider": "anthropic", "model": "claude-x"})
    assert "secreta" not in cli.get("/api/me/settings").text                          # la clave nunca se devuelve
    with db.pool().connection() as con:
        crudo = con.execute("SELECT keys::text AS k FROM user_settings WHERE sub='qa'").fetchone()["k"]
    assert "secreta" not in crudo and "anthropic" in crudo                            # y en la BD está cifrada
    assert account.decrypt(json.loads(crudo)["anthropic"]) == "sk-ant-secreta-1234"
    assert cli.delete("/api/me/key?provider=anthropic").json()["keys"] == {}
    assert cli.delete("/api/me").status_code == 204
    assert cli.get("/api/me/settings").json()["provider"] == "server"


def test_sin_clave_propia_se_vuelve_al_servidor(cli):
    cli.put("/api/me/settings", json={"provider": "openai", "model": "gpt-x"})
    from starlette.requests import Request
    req = Request({"type": "http", "headers": [], "query_string": b"", "path": "/", "method": "GET", "app": main.app})
    _, cfg = account.config_for(req)
    assert cfg.provider == "server"                                                   # elegido openai pero sin clave: nunca falla en silencio con otra cuenta


def test_asistente_usa_datos_del_lugar_y_el_proveedor_elegido(cli, monkeypatch):
    visto = {}

    async def comp(http, cfg, system, messages, **k):
        visto.update(cfg=cfg, system=system, messages=messages, model=k.get("model"))
        return "Mañana, lluvia débil."
    monkeypatch.setattr(ai, "complete", comp)
    cli.put("/api/me/key", json={"provider": "anthropic", "key": "sk-ant-abcdefgh"})
    cli.put("/api/me/settings", json={"provider": "anthropic", "model": "claude-sonnet-5-5"})
    r = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿Mañana?"}], "lat": 42.7, "lon": 0.9, "name": "Garòs"})
    assert r.status_code == 200 and r.json() == {"answer": "Mañana, lluvia débil.", "model": "claude-sonnet-5-5", "herramientas": [], "lugares": [], "tarjetas": []}
    assert visto["cfg"].provider == "anthropic" and visto["cfg"].api_key == "sk-ant-abcdefgh"
    assert "Garòs" in visto["system"] and '"tramos"' in visto["system"] and "por_modelo" not in visto["system"]
    assert cli.post("/api/assistant", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code in (200, 422, 502)
    assert cli.post("/api/assistant", json={"messages": []}).status_code == 422


def test_recarga_con_enfriamiento(cli):
    a = cli.get("/api/forecast?lat=42.7&lon=0.9").json()["generado"]
    time.sleep(1.1)
    assert cli.get("/api/forecast?lat=42.7&lon=0.9&refresh=1").json()["generado"] == a     # <5 min: devuelve la caché
    main._forecasts[main._key(42.7, 0.9)] = (time.time() - main.REFRESH_COOLDOWN - 5, main._forecasts[main._key(42.7, 0.9)][1])
    assert cli.get("/api/forecast?lat=42.7&lon=0.9&refresh=1").json()["generado"] > a      # pasado el enfriamiento, recalcula


async def test_mistral_habla_con_su_api_y_nunca_recibe_la_clave_de_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-de-openrouter")
    vistas = []

    def h(req):
        vistas.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola"}}]})
    async with _mock(h) as c:
        assert await ai.complete(c, ai.AIConfig("mistral", "mistral-small-latest", "clave-mistral"), "s", [{"role": "user", "content": "x"}]) == "hola"
        with pytest.raises(ValueError):                                              # sin clave propia: no se le manda la de OpenRouter
            await ai.complete(c, ai.AIConfig("mistral", "mistral-small-latest", None), "s", [{"role": "user", "content": "x"}])
    assert vistas == [("https://api.mistral.ai/v1/chat/completions", "Bearer clave-mistral")]


def test_con_clave_de_mistral_en_el_servidor_su_ia_es_mistral(cli, monkeypatch):
    s = cli.get("/api/me/settings").json()
    assert "mistral" in s["providers"] and "OpenRouter" in s["providers"]["server"]["label"]
    monkeypatch.setenv("MISTRAL_API_KEY", "clave-mistral-servidor")
    monkeypatch.setenv("MG_MISTRAL_MODEL", "mistral-medium-latest")
    s = cli.get("/api/me/settings").json()
    assert s["server_ai"] and s["provider"] == "server" and "Mistral" in s["providers"]["server"]["label"] and "clave-mistral" not in str(s)
    usadas = []

    async def falsa(http, cfg, system, messages, **k):
        usadas.append((cfg.provider, k.get("model") or cfg.model, cfg.api_key))
        return "OK"
    monkeypatch.setattr(ai, "complete", falsa)
    r = cli.post("/api/me/test").json()
    assert r["ok"] and r["provider"] == "mistral" and r["model"] == "mistral-medium-latest"
    assert usadas == [("mistral", "mistral-medium-latest", "clave-mistral-servidor")]
    monkeypatch.delenv("OPENROUTER_API_KEY")                                           # sin OpenRouter, el servidor sigue teniendo IA (Mistral)
    assert cli.get("/api/me/settings").json()["server_ai"]


def test_con_mistral_en_el_servidor_cada_persona_elige_su_modelo(cli, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "clave-mistral-servidor")
    monkeypatch.delenv("MG_MISTRAL_MODEL", raising=False)
    s = cli.get("/api/me/settings").json()
    assert s["server_mistral"] and s["server_model"] == "mistral-small-latest" and "mistral-large-latest" in s["providers"]["server"]["suggested"]
    usadas = []

    async def falsa(http, cfg, system, messages, **k):
        usadas.append(k.get("model") or cfg.model)
        return "OK"
    monkeypatch.setattr(ai, "complete", falsa)
    for elegido, esperado in (("mistral-large-latest", "mistral-large-latest"), ("google/gemma-3:free", "mistral-small-latest"), ("", "mistral-small-latest")):
        cli.put("/api/me/settings", json={"model": elegido})
        assert cli.post("/api/me/test").json()["model"] == esperado
    assert usadas == ["mistral-large-latest", "mistral-small-latest", "mistral-small-latest"]

    vistas = []

    async def lista(http, cfg):
        vistas.append((cfg.provider, cfg.api_key))
        return ["mistral-large-latest", "mistral-small-latest"]
    monkeypatch.setattr(ai, "list_models", lista)
    r = cli.get("/api/me/models?provider=server").json()
    assert r["models"] == ["mistral-large-latest", "mistral-small-latest"] and vistas == [("mistral", "clave-mistral-servidor")]
    assert "clave-mistral" not in str(r)


async def test_la_lista_de_modelos_de_mistral_quita_los_que_no_son_de_chat():
    def h(req):
        return httpx.Response(200, json={"data": [{"id": i} for i in ("mistral-small-latest", "mistral-embed", "mistral-ocr-latest", "mistral-moderation-latest", "pixtral-large-latest")]})
    async with _mock(h) as c:
        assert await ai.list_models(c, ai.AIConfig("mistral", "", "k")) == ["mistral-small-latest", "pixtral-large-latest"]
