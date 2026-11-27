"""Avisos personalizados por umbral (viento, racha, lluvia, oleaje)."""
import asyncio
from datetime import datetime, timezone

from meteoguilda import db, reglas
from tests.test_api import api  # noqa: F401
from tests.test_push import FCM, SUB, _limpia
from tests.test_v04 import cli  # noqa: F401


def _resumen(viento=20, racha=30, lluvia=0.0):
    return {"steps": [{"time": f"2031-01-05T{h:02d}:00", "wind_speed_10m": {"mean": viento + i}, "wind_gusts_10m": {"max": racha + 2 * i},
                       "precipitation": {"mean": lluvia}} for i, h in enumerate(range(12, 36, 3))][:8]}


def test_valor_previsto():
    r = _resumen(viento=20, racha=30, lluvia=2.5)
    assert reglas.valor_previsto("viento", r, None)[0] == 27
    assert reglas.valor_previsto("racha", r, None)[0] == 44
    assert reglas.valor_previsto("lluvia", r, None)[0] == 20.0
    mar = {"horas": [{"hora": "2031-01-05T10:00", "ola_m": 0.5}, {"hora": "2031-01-05T11:00", "ola_m": 1.8}, {"hora": "2031-01-05T12:00", "ola_m": None}]}
    assert reglas.valor_previsto("oleaje", None, mar) == (1.8, "2031-01-05T11:00")
    assert reglas.valor_previsto("viento", None, None) == (None, None) and reglas.valor_previsto("oleaje", None, {"horas": []}) == (None, None)


def test_mensaje_claro():
    t, c = reglas.mensaje("racha", 62.0, "2031-01-05T16:00", 50, "Premià de Mar")
    assert "Rachas" in t and "Premià" in t and "62 km/h hacia las 16:00" in c and "umbral: 50" in c


def test_crud_validacion_y_limites(cli, monkeypatch):
    _limpia(monkeypatch)
    regla = {"endpoint": FCM, "variable": "racha", "umbral": 60, "lat": 41.4923, "lon": 2.3611, "nombre": "Premià"}
    assert cli.put("/api/push/reglas", json=regla).status_code == 404            # sin dispositivo suscrito
    cli.put("/api/push/suscripcion", json=SUB)
    assert cli.put("/api/push/reglas", json={**regla, "variable": "calor"}).status_code == 422
    assert cli.put("/api/push/reglas", json={**regla, "umbral": 5}).status_code == 422
    assert cli.put("/api/push/reglas", json=regla).status_code == 200
    assert cli.put("/api/push/reglas", json={**regla, "umbral": 70}).status_code == 200          # misma regla: se actualiza, no se duplica
    lista = cli.post("/api/push/reglas/lista", json={"endpoint": FCM}).json()
    assert len(lista["reglas"]) == 1 and lista["reglas"][0]["umbral"] == 70 and lista["reglas"][0]["lat"] == 41.49
    for i, v in enumerate(["viento", "lluvia", "oleaje", "racha"]):
        cli.put("/api/push/reglas", json={**regla, "variable": v, "umbral": 20 if v != "oleaje" else 1, "lat": 40 + i})
    assert cli.put("/api/push/reglas", json={**regla, "variable": "viento", "umbral": 30, "lat": 10}).status_code == 409       # máximo 5
    rid = lista["reglas"][0]["id"]
    assert cli.delete(f"/api/push/reglas/{rid}").status_code == 200 and cli.delete(f"/api/push/reglas/{rid}").status_code == 404
    cli.request("DELETE", "/api/push/suscripcion", json={"endpoint": FCM})
    assert cli.post("/api/push/reglas/lista", json={"endpoint": FCM}).json()["reglas"] == []
    with db.pool().connection() as con:
        assert con.execute("SELECT count(*) AS n FROM push_reglas").fetchone()["n"] == 0          # se borran con el dispositivo


def test_revisar_encola_una_vez_al_dia(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.put("/api/push/reglas", json={"endpoint": FCM, "variable": "racha", "umbral": 40, "lat": 41.49, "lon": 2.36, "nombre": "Premià"})
    cli.put("/api/push/reglas", json={"endpoint": FCM, "variable": "lluvia", "umbral": 100, "lat": 41.49, "lon": 2.36})
    llamadas = []

    async def resumen(la, lo):
        llamadas.append((la, lo)); return _resumen(racha=30)

    async def mar(la, lo):
        return {}
    ahora = datetime(2031, 1, 5, 9, tzinfo=timezone.utc)
    assert asyncio.run(reglas.revisar(resumen, mar, ahora)) == 1          # racha (44) ≥ 40 sí; lluvia (0) < 100 no
    assert len(llamadas) == 1                                              # una sola consulta para las dos reglas del mismo lugar
    assert asyncio.run(reglas.revisar(resumen, mar, ahora)) == 0          # mismo día: no se repite
    assert asyncio.run(reglas.revisar(resumen, mar, datetime(2031, 1, 6, 9, tzinfo=timezone.utc))) == 1      # otro día: sí
    with db.pool().connection() as con:
        t = con.execute("SELECT titulo FROM push_cola ORDER BY id LIMIT 1").fetchone()["titulo"]
    assert "Rachas" in t


def test_una_fuente_caida_no_para_las_demas(cli, monkeypatch):
    _limpia(monkeypatch)
    cli.put("/api/push/suscripcion", json=SUB)
    cli.put("/api/push/reglas", json={"endpoint": FCM, "variable": "viento", "umbral": 10, "lat": 41.49, "lon": 2.36})

    async def rota(la, lo):
        raise RuntimeError("fuente caída")
    assert asyncio.run(reglas.revisar(rota, rota, datetime(2031, 1, 5, tzinfo=timezone.utc))) == 0
