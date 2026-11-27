"""Verificación ampliada: lugares extra de cada persona (con topes y respeto a la cuota) e informe mensual."""
import asyncio
from datetime import datetime, timedelta, timezone

from meteoguilda import db, verif
from meteoguilda.places import PLACES
from tests.test_api import api  # noqa: F401
from tests.test_v04 import cli  # noqa: F401
from tests.test_verif import _raw

UTC = timezone.utc


def _limpia():
    with db.pool().connection() as con:
        con.execute("DELETE FROM verif_pred; TRUNCATE verif_extra CASCADE")
    verif.extras(refrescar=True)
    verif._ultimo_tramo = None


def test_mis_lugares_topes_y_borrado(cli, monkeypatch):
    _limpia()
    assert cli.get("/api/verificacion/mis-lugares").json()["lugares"] == []
    r = cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49231, "lon": 2.36118, "nombre": "Casa"}, {"lat": 42.0, "lon": 1.0}]}).json()
    assert [x["clave"] for x in r["lugares"]] == ["x:41.49,2.36", "x:42.00,1.00"] and r["lugares"][1]["nombre"] == "42.00, 1.00"
    assert cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 1, "lon": 1}] * 6}).status_code == 422          # máximo 5
    assert verif.lugar_de(41.49, 2.36) == "x:41.49,2.36" and verif.lugar_de(41.4923, 2.3612) is None
    monkeypatch.setattr(verif, "MAX_EXTRA_TOTAL", 3)
    with db.pool().connection() as con:                                   # otra persona ya ocupa dos plazas
        for i, k in enumerate(["x:10.00,10.00", "x:11.00,11.00"]):
            con.execute("INSERT INTO verif_extra (clave, lat, lon, nombre) VALUES (%s,%s,%s,'otro')", [k, 10 + i, 10 + i])
            con.execute("INSERT INTO verif_extra_sub (sub, clave) VALUES ('otra', %s)", [k])
    assert cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49, "lon": 2.36}, {"lat": 42, "lon": 1}]}).status_code == 409
    assert cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49, "lon": 2.36}]}).status_code == 200
    with db.pool().connection() as con:
        assert con.execute("SELECT count(*) AS n FROM verif_extra").fetchone()["n"] == 3                 # el que nadie sigue se va
    cli.delete("/api/me")
    assert cli.get("/api/verificacion/mis-lugares").json()["lugares"] == []
    _limpia()


def test_registrar_extras_una_vez_por_tramo_y_respetando_la_cuota(cli):
    _limpia()
    cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49, "lon": 2.36}, {"lat": 42.0, "lon": 1.0}]})
    pedidos = []

    async def obtener(la, lo):
        pedidos.append((la, lo))
        verif.registrar(_raw(), verif.lugar_de(la, lo), datetime(2026, 11, 26, 6, 10, tzinfo=UTC))
    holgada = lambda: {"porcentaje": {"dia": 12.0}, "frenado": False}  # noqa: E731
    justa = lambda: {"porcentaje": {"dia": 75.0}, "frenado": False}  # noqa: E731
    assert asyncio.run(verif.registrar_extras(obtener, holgada, datetime(2026, 11, 26, 7, 10, tzinfo=UTC), 0)) == 0         # no es hora de registro
    assert asyncio.run(verif.registrar_extras(obtener, justa, datetime(2026, 11, 26, 6, 10, tzinfo=UTC), 0)) == 0           # cuota justa: se salta
    assert asyncio.run(verif.registrar_extras(obtener, holgada, datetime(2026, 11, 26, 6, 20, tzinfo=UTC), 0)) == 0         # mismo tramo: no se repite
    assert asyncio.run(verif.registrar_extras(obtener, holgada, datetime(2026, 11, 26, 12, 5, tzinfo=UTC), 0)) == 2
    assert pedidos == [(41.49, 2.36), (42.0, 1.0)]
    with db.pool().connection() as con:
        assert con.execute("SELECT count(*) AS n FROM verif_pred WHERE lugar = 'x:41.49,2.36'").fetchone()["n"] == 4
    _limpia()


def _siembra(lugar, maes, dias, ini):
    with db.pool().connection() as con:
        for m, mae in maes.items():
            for d in range(dias):
                for lead in (12, 24):
                    con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c, obs_c) VALUES (%s,%s,%s,%s,%s,10)",
                                [lugar, m, lead, ini + timedelta(days=d, hours=12), 10 + mae])


def test_informe_mensual_por_lugar_y_general(cli):
    _limpia()
    cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49, "lon": 2.36, "nombre": "Casa"}]})
    slug = PLACES[0].slug
    _siembra(slug, {"icon_seamless": 0.5, "gfs_seamless": 2.0}, 10, datetime(2026, 10, 1, tzinfo=UTC))
    _siembra(slug, {"icon_seamless": 1.0, "gfs_seamless": -0.4}, 6, datetime(2026, 11, 1, tzinfo=UTC))
    _siembra("x:41.49,2.36", {"icon_seamless": 3.0, "gfs_seamless": 0.2}, 5, datetime(2026, 11, 1, tzinfo=UTC))
    _siembra("x:41.49,2.36", {"knmi_seamless": 0.1}, 2, datetime(2026, 11, 1, tzinfo=UTC))            # pocas comparaciones: no entra
    r = cli.get("/api/verificacion/informe?mes=2026-11").json()
    assert r["mes"] == "2026-11" and r["desde"] == "2026-11-01"
    por = {x["lugar"]: x for x in r["lugares"]}
    p = por[slug]
    assert p["ganador"] == "NOAA GFS" and p["a_24h"][0]["mae_c"] == 0.4 and p["a_24h"][0]["sesgo_c"] == -0.4 and p["a_24h"][0]["acierto_2c_pct"] == 100
    assert p["a_24h"][0]["mae_mes_anterior_c"] == 2.0 and p["dias_con_datos"] == 6 and p["principal"]
    c = por["x:41.49,2.36"]
    assert c["nombre"] == "Casa" and not c["principal"] and [m["modelo"] for m in c["a_24h"]] == ["gfs_seamless", "icon_seamless"]
    assert r["general"][0]["modelo"] == "gfs_seamless" and r["general"][0]["n"] == 11 and r["ganador_general"] == "NOAA GFS"
    sin = [x for x in r["lugares"] if x["lugar"] == PLACES[1].slug][0]
    assert sin["ganador"] is None and "comparaciones" in sin["nota"]
    assert cli.get("/api/verificacion/informe?mes=2026-13").status_code == 422
    _limpia()


def test_precision_usa_el_lugar_extra_cercano(cli):
    _limpia()
    cli.put("/api/verificacion/mis-lugares", json={"lugares": [{"lat": 41.49, "lon": 2.36, "nombre": "Casa"}]})
    _siembra("x:41.49,2.36", {"icon_seamless": 0.3}, 10, datetime.now(UTC) - timedelta(days=12))
    r = cli.get("/api/precision?lat=41.5&lon=2.37").json()
    assert r["disponible"] and r["lugar"] == "Casa" and r["modelos"][0]["modelo"] == "icon_seamless"
    _limpia()
