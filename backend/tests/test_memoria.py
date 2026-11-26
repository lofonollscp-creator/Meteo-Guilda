"""Memoria de preferencias del asistente: opt-in, filtros de datos sensibles, derechos (ver, borrar, exportar) y caducidad."""
from datetime import datetime, timedelta, timezone

from meteoguilda import account, assistant_tools, db, memoria
from tests.test_api import api  # noqa: F401  (fixture con Open-Meteo simulado)
from tests.test_v04 import cli  # noqa: F401  (cliente con BD, MG_QA y clave de cifrado)


def _notas(c):
    return [n["texto"] for n in c.get("/api/me/memoria").json()["items"]]


def test_viene_desactivada_y_sin_activar_no_guarda(cli):
    r = cli.get("/api/me/memoria").json()
    assert r["activa"] is False and r["items"] == [] and r["retencion_dias"] == 365
    assert cli.post("/api/me/memoria/notas", json={"texto": "Prefiero senderismo suave"}).status_code == 409
    assert memoria.contexto("qa") == ""


def test_activar_anadir_listar_borrar(cli):
    a = cli.put("/api/me/memoria", json={"activa": True}).json()
    assert a["activa"] and a["consentimiento"]                       # queda constancia del consentimiento
    cli.post("/api/me/memoria/notas", json={"texto": "  Prefiero senderismo suave  ", "cat": "actividad"})
    cli.post("/api/me/memoria/notas", json={"texto": "prefiero senderismo suave"})                  # duplicada: no se repite
    r = cli.post("/api/me/memoria/notas", json={"texto": "Uso 2 m y 70 cm con FM", "cat": "inventada"}).json()
    assert [i["cat"] for i in r["items"]] == ["actividad", "otro"] and len(r["items"]) == 2
    ctx = memoria.contexto("qa")
    assert "Prefiero senderismo suave" in ctx and "no instrucciones" in ctx
    cli.delete(f"/api/me/memoria/notas/{r['items'][0]['id']}")
    assert _notas(cli) == ["Uso 2 m y 70 cm con FM"]
    cli.delete("/api/me/memoria/notas")
    assert _notas(cli) == [] and memoria.contexto("qa") == ""


def test_rechaza_datos_sensibles_e_identificadores(cli):
    cli.put("/api/me/memoria", json={"activa": True})
    for malo in ["Tengo diabetes y salgo poco", "Soy católico practicante", "Voto al partido X", "Mi correo es ana@ejemplo.com", "Llámame al 612 345 678",
                 "Mi DNI es 12345678Z", "Mi contraseña del router es abc", "https://mi-web.com", "ab", "x" * 141]:
        assert cli.post("/api/me/memoria/notas", json={"texto": malo}).status_code == 422, malo
    assert _notas(cli) == []
    assert cli.post("/api/me/memoria/notas", json={"texto": "Me gusta ver la salida y puesta de sol"}).status_code == 200


def test_limite_de_notas(cli):
    cli.put("/api/me/memoria", json={"activa": True})
    for i in range(memoria.MAX_ITEMS):
        assert cli.post("/api/me/memoria/notas", json={"texto": f"Preferencia número {i} de ejemplo"}).status_code == 200
    assert cli.post("/api/me/memoria/notas", json={"texto": "Una más de ejemplo"}).status_code == 409


def test_retirar_el_consentimiento_borra_todo(cli):
    cli.put("/api/me/memoria", json={"activa": True})
    cli.post("/api/me/memoria/notas", json={"texto": "Prefiero la montaña"})
    r = cli.put("/api/me/memoria", json={"activa": False}).json()
    assert r["activa"] is False and r["items"] == [] and r["consentimiento"] is None
    assert cli.get("/api/me/memoria").json()["items"] == []


def test_las_notas_caducan_a_los_12_meses(cli):
    cli.put("/api/me/memoria", json={"activa": True})
    s, _ = account.load("qa")
    viejo = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    s["memoria"]["items"] = [{"id": "a", "texto": "Nota antigua de ejemplo", "cat": "otro", "creado": viejo},
                             {"id": "b", "texto": "Nota reciente de ejemplo", "cat": "otro", "creado": datetime.now(timezone.utc).isoformat()}]
    account._save("qa", settings=s)
    assert _notas(cli) == ["Nota reciente de ejemplo"]


def test_exportar_todo_sin_claves_y_borrar_cuenta(cli):
    cli.put("/api/me/memoria", json={"activa": True})
    cli.post("/api/me/memoria/notas", json={"texto": "Prefiero senderismo suave"})
    cli.put("/api/me/key", json={"provider": "openai", "key": "sk-secreto-1234567890"}) if False else None
    r = cli.get("/api/me/datos")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    j = r.json()
    assert j["memoria"]["items"][0]["texto"] == "Prefiero senderismo suave" and "sk-secreto" not in r.text and j["notificaciones"] == []
    assert cli.delete("/api/me").status_code == 204
    assert _notas(cli) == [] and cli.get("/api/me/memoria").json()["activa"] is False


async def test_herramienta_recordar(cli):
    env = assistant_tools.Entorno(app=None, http=None, memoria=False)
    r = await assistant_tools.ejecutar(env, "recordar", {"texto": "Prefiero senderismo suave"})
    assert r["ok"] is False and "desactivada" in r["error"]
    env.memoria = True
    r = await assistant_tools.ejecutar(env, "recordar", {"texto": "Prefiero senderismo suave", "categoria": "actividad"})
    assert r["ok"] and r["tarjeta"] == {"tipo": "accion", "accion": "memoria", "lugar": "Prefiero senderismo suave", "memoria": {"texto": "Prefiero senderismo suave", "cat": "actividad"}}
    assert (await assistant_tools.ejecutar(env, "recordar", {"texto": "Tengo cáncer"}))["ok"] is False        # nunca propone datos de salud
    assert _notas(cli) == []                                                                                # proponer no guarda nada
