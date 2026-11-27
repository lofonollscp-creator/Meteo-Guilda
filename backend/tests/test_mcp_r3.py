"""MCP: descargas por enlace firmado (ADIF y respuestas grandes) y confirmación por elicitación antes de borrar contactos."""
import asyncio
import time

import httpx
import pytest

from meteoguilda import descargas, logbook, main, mcp_server
from tests.test_api import api  # noqa: F401
from tests.test_v04 import cli  # noqa: F401


@pytest.fixture
def mcp_api(cli, monkeypatch):
    monkeypatch.setenv("MG_ADMIN_TOKEN", "ci-admin")
    monkeypatch.setenv("MG_PUBLIC_URL", "https://radio.example")
    monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t"))
    key = logbook.create_profile()
    h = logbook.auth(key)
    ids = logbook.add_many(h, [logbook.clean({"call": c, "qso_at": "2026-11-20T18:30:00Z", "band": "20m", "mode": "SSB"}) for c in ("W1AW", "EA3XYZ")])
    return key, h, ids


def _token(enlace):
    assert enlace.startswith("https://radio.example/api/descargar/")
    return enlace.rsplit("/", 1)[1]


def test_exportar_adif_da_un_enlace_que_caduca(cli, mcp_api, monkeypatch):
    key, _, _ = mcp_api
    r = asyncio.run(mcp_server.mcp.call_tool("logbook_export_adif", {"key": key}))
    d = r.structured_content if hasattr(r, "structured_content") and r.structured_content else None
    texto = str(r)
    assert "<CALL:4>W1AW" not in texto and "caduca_en_minutos" in texto and "'contactos': 2" in texto.replace('"', "'")
    enlace = (d or {}).get("enlace") or texto.split("https://radio.example", 1)[1].split("'")[0].split('"')[0]
    tok = _token(enlace if enlace.startswith("https") else "https://radio.example" + enlace)
    g = cli.get(f"/api/descargar/{tok}")
    assert g.status_code == 200 and "<CALL:4>W1AW" in g.text and "attachment" in g.headers["content-disposition"] and g.headers["cache-control"] == "no-store"
    assert cli.get(f"/api/descargar/{tok}").status_code == 200                     # se puede repetir mientras no caduque
    assert cli.get(f"/api/descargar/{tok[:-2]}xx").status_code == 404               # firma manipulada
    monkeypatch.setattr(descargas.time, "time", lambda: time.time_ns() / 1e9 + 3601)
    assert cli.get(f"/api/descargar/{tok}").status_code == 404                      # caducado


def test_guardar_descargas_solo_con_la_clave_interna(cli, mcp_api):
    assert cli.post("/api/descargas", json={"nombre": "x.txt", "contenido": "hola"}).status_code == 403
    r = cli.post("/api/descargas", json={"nombre": "../../etc/x.txt", "contenido": "hola"}, headers={"X-Admin-Token": "ci-admin"}).json()
    assert r["nombre_archivo"] == "x.txt" and r["tamano"] == 4


def test_respuestas_grandes_van_por_enlace(cli, mcp_api, monkeypatch):
    key, h, _ = mcp_api
    monkeypatch.setattr(mcp_server, "UMBRAL_ENLACE", 300)
    texto = str(asyncio.run(mcp_server.mcp.call_tool("logbook_list", {"key": key})))
    assert "respuesta_grande" in texto and "descarga" in texto and "'elementos': 2" in texto.replace('"', "'")
    monkeypatch.setattr(mcp_server, "UMBRAL_ENLACE", 60_000)
    texto = str(asyncio.run(mcp_server.mcp.call_tool("logbook_list", {"key": key})))
    assert "respuesta_grande" not in texto and "W1AW" in texto


def _llamar(nombre, argumentos, respuesta, modo):
    """`legacy`: cliente anterior (el servidor pregunta en mitad de la llamada); `auto`: protocolo 2026-07-28 (`input_required` y repetición)."""
    from mcp import types
    from mcp.client import Client
    preguntas = []

    async def elicitar(_ctx, params):
        preguntas.append(params.message)
        return types.ElicitResult(action=respuesta, content={"confirmar": True} if respuesta == "accept" else None)

    async def principal():
        async with Client(mcp_server.mcp, elicitation_callback=elicitar, mode=modo) as c:
            return await c.call_tool(nombre, argumentos)
    return asyncio.run(principal()), preguntas


@pytest.mark.parametrize("modo", ["legacy", "auto"])
def test_borrar_contacto_pide_confirmacion(cli, mcp_api, modo):
    key, h, ids = mcp_api
    r, preguntas = _llamar("logbook_delete", {"qso_id": ids[0], "key": key}, "decline", modo)
    assert r.is_error and "no lo ha confirmado" in r.content[0].text
    assert preguntas and "W1AW" in preguntas[0] and "No se puede deshacer" in preguntas[0]
    assert len(logbook.list_qsos(h)) == 2
    r, _ = _llamar("logbook_delete", {"qso_id": ids[0], "key": key}, "accept", modo)
    assert not r.is_error and [q["call"] for q in logbook.list_qsos(h)] == ["EA3XYZ"]


def test_sin_elicitacion_confia_en_la_aprobacion_del_cliente(cli, mcp_api):
    from mcp.client import Client
    key, h, ids = mcp_api

    async def principal():
        async with Client(mcp_server.mcp, mode="auto") as c:
            herr = {t.name: t for t in (await c.list_tools()).tools}
            assert herr["logbook_delete"].annotations.destructive_hint is True
            assert "confirmacion" not in str(herr["logbook_delete"].input_schema)        # el modelo no ve el parámetro
            return await c.call_tool("logbook_delete", {"qso_id": ids[1], "key": key})
    r = asyncio.run(principal())
    assert not r.is_error and [q["call"] for q in logbook.list_qsos(h)] == ["W1AW"]


def test_las_de_lectura_no_preguntan(cli, mcp_api):
    key, _, _ = mcp_api
    r, preguntas = _llamar("logbook_stats", {"key": key}, "decline", "auto")
    assert not r.is_error and preguntas == []
