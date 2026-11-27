"""Alertas de radio y salidas: validación, tope diario, silencio, una vez al día, pasos de satélite, rutas guardadas y fuentes caídas."""
import asyncio
from datetime import datetime, timedelta, timezone

from meteoguilda import alertas, db
from tests.test_api import api  # noqa: F401
from tests.test_push import FCM, SUB, _limpia
from tests.test_reglas import _resumen
from tests.test_v04 import cli  # noqa: F401

AHORA = datetime(2031, 1, 5, 9, tzinfo=timezone.utc)        # 10:00 en Madrid


def _fuentes(llamadas=None, kp=2.0, sfi=160.0, tropo_cat="super-refracción", pasos=None, avisos=None, racha=30, falla=()):
    llamadas = llamadas if llamadas is not None else []

    def f(nombre, valor):
        async def g(*a):
            llamadas.append((nombre, *a))
            if nombre in falla:
                raise RuntimeError("caída")
            return valor(*a) if callable(valor) else valor
        return g
    serie = [{"time": f"2031-01-05T{h:02d}:00", "gradient": -120 if (h == 18 and tropo_cat) else -40, "cat": tropo_cat if h == 18 and tropo_cat else "normal"} for h in range(10, 34) if h < 24]
    return alertas.Fuentes(resumen=f("resumen", _resumen(racha=racha)), mar=f("mar", {"horas": []}), tropo=f("tropo", {"serie": serie}),
                           solar=f("solar", {"kp": kp, "sfi": sfi}), pasos=f("pasos", pasos or []), avisos=f("avisos", avisos))


def _limpia_alertas(monkeypatch):
    _limpia(monkeypatch)
    with db.pool().connection() as con:
        con.execute("TRUNCATE alertas, alertas_prefs, alertas_envios, rutas_guardadas")


def _cola():
    with db.pool().connection() as con:
        return con.execute("SELECT titulo, cuerpo, cuando FROM push_cola ORDER BY id").fetchall()


def test_crear_validar_y_limites(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    d = cli.get("/api/alertas").json()
    assert d["alertas"] == [] and d["prefs"]["max_dia"] == 6 and d["dispositivos"] == 0 and "tropo" in d["tipos"]
    assert cli.post("/api/alertas", json={"tipo": "tropo", "params": {"lat": 41.49231, "lon": 2.36}}).json()["params"]["lat"] == 41.49
    assert cli.post("/api/alertas", json={"tipo": "tropo", "params": {"lat": 141, "lon": 2}}).status_code == 422
    assert cli.post("/api/alertas", json={"tipo": "inventado", "params": {}}).status_code == 422
    assert cli.post("/api/alertas", json={"tipo": "meteo", "params": {"lat": 41, "lon": 2, "variable": "racha", "umbral": 5}}).status_code == 422
    assert cli.post("/api/alertas", json={"tipo": "meteo", "params": {"lat": 41, "lon": 2, "variable": "aviso", "umbral": 2}}).status_code == 201
    assert cli.post("/api/alertas", json={"tipo": "satelite", "params": {"lat": 41, "lon": 2, "norad": []}}).status_code == 422
    sat = cli.post("/api/alertas", json={"tipo": "satelite", "params": {"lat": 41, "lon": 2, "norad": [43017, 25544, 43017]}}).json()
    assert sat["params"]["norad"] == [25544, 43017] and sat["params"]["antelacion_min"] == 10
    assert cli.post("/api/alertas", json={"tipo": "ruta", "params": {"ruta_id": 999, "variable": "racha", "umbral": 50}}).status_code == 404
    hf = cli.post("/api/alertas", json={"tipo": "hf", "params": {}}).json()
    assert cli.patch(f"/api/alertas/{hf['id']}", json={"activa": False}).json()["activa"] is False
    assert cli.delete(f"/api/alertas/{hf['id']}").status_code == 200 and cli.delete(f"/api/alertas/{hf['id']}").status_code == 404
    for i in range(alertas.MAX_ALERTAS - 3):
        assert cli.post("/api/alertas", json={"tipo": "hf", "params": {"sfi_min": 100 + i}}).status_code == 201
    assert cli.post("/api/alertas", json={"tipo": "hf", "params": {}}).status_code == 409


def test_prefs_silencio_y_zona(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    assert cli.put("/api/alertas/prefs", json={"max_dia": 0}).status_code == 422
    assert cli.put("/api/alertas/prefs", json={"silencio_desde": 23}).status_code == 422
    assert cli.put("/api/alertas/prefs", json={"zona": "Marte/Olimpo"}).status_code == 422
    p = cli.put("/api/alertas/prefs", json={"max_dia": 3, "silencio_desde": 23, "silencio_hasta": 7}).json()["prefs"]
    assert p == {"max_dia": 3, "silencio_desde": 23, "silencio_hasta": 7, "zona": "Europe/Madrid"}
    assert alertas.en_silencio(p, datetime(2031, 1, 5, 23, 30, tzinfo=timezone.utc))         # 00:30 en Madrid
    assert alertas.en_silencio(p, datetime(2031, 1, 5, 5, 0, tzinfo=timezone.utc))
    assert not alertas.en_silencio(p, datetime(2031, 1, 5, 7, 0, tzinfo=timezone.utc))        # 08:00 en Madrid
    assert cli.put("/api/alertas/prefs", json={"sin_silencio": True}).json()["prefs"]["silencio_desde"] is None


def test_revisar_envia_a_todos_los_dispositivos_una_vez_al_dia(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.put("/api/push/suscripcion", json={**SUB, "endpoint": FCM + "-movil"})
    cli.post("/api/alertas", json={"tipo": "tropo", "params": {"lat": 41.49, "lon": 2.36, "nombre": "QTH"}})
    cli.post("/api/alertas", json={"tipo": "hf", "params": {"sfi_min": 150, "kp_max": 3}})
    cli.post("/api/alertas", json={"tipo": "meteo", "params": {"lat": 41.49, "lon": 2.36, "variable": "racha", "umbral": 40}})
    llamadas = []
    r = asyncio.run(alertas.revisar(_fuentes(llamadas), AHORA))
    assert r == {"ok": 3}
    cola = _cola()
    assert len(cola) == 6                                            # 3 avisos × 2 dispositivos
    titulos = {c["titulo"] for c in cola}
    assert any("tropo" in t and "QTH" in t for t in titulos) and "Buenas condiciones de HF" in titulos and any("Rachas" in t for t in titulos)
    assert asyncio.run(alertas.revisar(_fuentes(), AHORA + timedelta(hours=1))) == {"repetida": 3}
    assert asyncio.run(alertas.revisar(_fuentes(), AHORA + timedelta(days=1))) == {"ok": 3}


def test_tope_diario_y_silencio(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.put("/api/alertas/prefs", json={"max_dia": 2, "silencio_desde": 22, "silencio_hasta": 8})
    for i in range(3):
        cli.post("/api/alertas", json={"tipo": "hf", "params": {"sfi_min": 100 + i}})
    noche = datetime(2031, 1, 5, 23, tzinfo=timezone.utc)                       # medianoche en Madrid: silencio
    assert asyncio.run(alertas.revisar(_fuentes(), noche)) == {"silencio": 3} and _cola() == []
    assert asyncio.run(alertas.revisar(_fuentes(), AHORA)) == {"ok": 2, "tope": 1}         # tras el silencio sí, hasta el tope
    assert len(_cola()) == 2


def test_hf_mejora_y_tormenta(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.post("/api/alertas", json={"tipo": "hf", "params": {"sfi_min": 150, "kp_max": 3}})
    cli.post("/api/alertas", json={"tipo": "hf", "params": {"modo": "tormenta", "kp_min": 6}})
    assert asyncio.run(alertas.revisar(_fuentes(kp=4, sfi=170), AHORA)) == {}                # Kp 4: ni mejora ni tormenta
    assert asyncio.run(alertas.revisar(_fuentes(kp=7, sfi=170), AHORA)) == {"ok": 1}
    assert "Tormenta geomagnética: Kp 7" in _cola()[0]["titulo"]


def test_pasos_de_satelite_con_antelacion(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.post("/api/alertas", json={"tipo": "satelite", "params": {"lat": 41.49, "lon": 2.36, "norad": [25544], "min_el": 30, "antelacion_min": 15}})
    aos = AHORA + timedelta(minutes=50)
    pasos = [{"satelite": "ISS (ZARYA)", "norad": 25544, "aos": aos.isoformat(), "aos_az": 300, "max_el": 62, "duracion_s": 600},
             {"satelite": "ISS (ZARYA)", "norad": 25544, "aos": (AHORA - timedelta(minutes=2)).isoformat(), "aos_az": 10, "max_el": 40, "duracion_s": 500}]
    llamadas = []
    assert asyncio.run(alertas.revisar(_fuentes(llamadas, pasos=pasos), AHORA)) == {"ok": 1}
    assert ("pasos", 41.49, 2.36, [25544], 3.0, 30.0) in llamadas
    c = _cola()[0]
    assert c["titulo"] == "Paso de ISS (ZARYA) a las 10:50" and "62°" in c["cuerpo"] and "NO" in c["cuerpo"] and c["cuando"] == aos - timedelta(minutes=15)
    assert asyncio.run(alertas.revisar(_fuentes(pasos=pasos), AHORA + timedelta(minutes=15))) == {"repetida": 1}


def test_ruta_guardada_consulta_pocos_puntos(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    wps = [{"lat": 42.6 + i * 0.01, "lon": 0.8, **({"nombre": "Collado"} if i == 9 else {})} for i in range(10)]
    assert cli.post("/api/rutas", json={"nombre": "Aneto", "modo": "foot", "waypoints": wps[:1]}).status_code == 422
    rid = cli.post("/api/rutas", json={"nombre": "Aneto", "modo": "foot", "waypoints": wps}).json()["id"]
    assert cli.post("/api/rutas", json={"nombre": "aneto", "modo": "car", "waypoints": wps}).json() == {"id": rid, "sustituida": True}
    assert cli.post("/api/alertas", json={"tipo": "ruta", "params": {"ruta_id": rid, "variable": "racha", "umbral": 40}}).status_code == 201
    llamadas = []
    assert asyncio.run(alertas.revisar(_fuentes(llamadas), AHORA)) == {"ok": 1}
    assert len([x for x in llamadas if x[0] == "resumen"]) == 4                               # 10 puntos → 4 consultas
    c = _cola()[0]
    assert "Ruta «Aneto»" in c["titulo"] and "cerca de salida" in c["cuerpo"]
    assert cli.delete(f"/api/rutas/{rid}").status_code == 200
    assert cli.get("/api/alertas").json()["alertas"] == []                                     # se van con la ruta


def test_fuente_caida_y_avisos_oficiales(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.post("/api/alertas", json={"tipo": "tropo", "params": {"lat": 41.49, "lon": 2.36}})
    cli.post("/api/alertas", json={"tipo": "meteo", "params": {"lat": 41.49, "lon": 2.36, "variable": "aviso", "umbral": 2}})
    av = [{"evento": "Vientos", "zona": "Litoral sur de Barcelona", "nivel": 2, "desde": "2031-01-05T12:00:00+00:00", "hasta": "2031-01-05T20:00:00+00:00",
           "poligonos": [[(41.0, 2.0), (42.0, 2.0), (42.0, 3.0), (41.0, 3.0)]]}]
    r = asyncio.run(alertas.revisar(_fuentes(avisos=av, falla=("tropo",)), AHORA))
    assert r == {"error": 1, "ok": 1}
    c = _cola()[0]
    assert c["titulo"] == "Aviso naranja: Vientos" and "hasta 21:00" in c["cuerpo"]


def test_sin_dispositivos_no_se_revisa(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    cli.post("/api/alertas", json={"tipo": "hf", "params": {}})
    llamadas = []
    assert asyncio.run(alertas.revisar(_fuentes(llamadas), AHORA)) == {} and llamadas == []


def test_comprobar_no_envia(cli, monkeypatch):
    _limpia_alertas(monkeypatch)
    a = cli.post("/api/alertas", json={"tipo": "hf", "params": {"sfi_min": 100}}).json()
    cli.app.state.fuentes_alertas = _fuentes()
    r = cli.get(f"/api/alertas/{a['id']}/comprobar").json()
    assert r["saltaria"] and r["avisos"][0]["titulo"] == "Buenas condiciones de HF" and r["tope_alcanzado"] is False
    assert _cola() == []
    datos = cli.get("/api/me/datos").json()
    assert datos["alertas_radio_y_salidas"]["alertas"][0]["tipo"] == "hf"
    cli.delete("/api/me")
    assert cli.get("/api/alertas").json()["alertas"] == []


async def test_mcp_alertas_y_rutas_en_nombre_de_la_persona(api, monkeypatch):  # noqa: F811
    import httpx

    from meteoguilda import main, mcp_server
    monkeypatch.setenv("MG_ADMIN_TOKEN", "ci-admin")
    with db.pool().connection() as con:
        con.execute("TRUNCATE alertas, alertas_prefs, alertas_envios, rutas_guardadas")
    nombres = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"list_alerts", "create_alert", "update_alert", "delete_alert", "set_alert_limits", "check_alert", "list_saved_routes", "save_route", "delete_saved_route"} <= nombres
    monkeypatch.setattr(mcp_server, "_who", lambda ctx: {"sub": "mcp-ana", "email": "ana@x.test"})
    async with main.app.router.lifespan_context(main.app):
        monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t"))
        r = await mcp_server.mcp.call_tool("save_route", {"name": "Costa", "mode": "sea", "waypoints": [{"lat": 41.3, "lon": 2.2}, {"lat": 41.5, "lon": 2.5}]})
        assert "Costa" not in str(r) and "id" in str(r)
        await mcp_server.mcp.call_tool("create_alert", {"tipo": "hf", "params": {"sfi_min": 140}})
        await mcp_server.mcp.call_tool("set_alert_limits", {"max_per_day": 4, "quiet_from_hour": 22, "quiet_to_hour": 8})
        res = str(await mcp_server.mcp.call_tool("list_alerts", {}))
        assert "140" in res and "'max_dia': 4" in res.replace('"', "'")
    with db.pool().connection() as con:
        assert con.execute("SELECT sub FROM alertas").fetchone()["sub"] == "mcp-ana"
        assert con.execute("SELECT modo FROM rutas_guardadas").fetchone()["modo"] == "sea"
