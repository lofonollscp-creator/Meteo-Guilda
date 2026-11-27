"""Verificación de modelos: registro a 12/24 h, comparación con estaciones oficiales y ranking por error medio."""
from datetime import datetime, timedelta, timezone

from meteoguilda import db, sources, verif
from meteoguilda.places import PLACES

SLUG = PLACES[0].slug
UTC = timezone.utc


def _limpia():
    with db.pool().connection() as con:
        con.execute("DELETE FROM verif_pred")


def _raw():
    base = datetime(2026, 11, 26, 0, 0)       # hora local (UTC+1)
    times = [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(72)]
    return {"utc_offset_seconds": 3600, "hourly": {"time": times,
            "temperature_2m_icon_seamless": [10.0 + i * 0.1 for i in range(72)],
            "temperature_2m_gfs_seamless": [9.0 + i * 0.1 for i in range(72)],
            "wind_speed_10m_icon_seamless": [3.0] * 72}}          # otra variable: se ignora


def test_registrar_solo_a_las_horas_de_registro(pg):
    _limpia()
    assert verif.registrar(_raw(), SLUG, datetime(2026, 11, 26, 7, 0, tzinfo=UTC)) == 0
    n = verif.registrar(_raw(), SLUG, datetime(2026, 11, 26, 6, 10, tzinfo=UTC))
    assert n == 4                                                     # 2 modelos × 12 h y 24 h
    with db.pool().connection() as con:
        filas = con.execute("SELECT modelo, lead_h, valida, pred_c FROM verif_pred WHERE modelo = 'icon_seamless' ORDER BY lead_h").fetchall()
    # a las 06:10 UTC = 07:10 locales → hora actual 07:00 locales (índice 7); +12 h = índice 19 (19:00 locales = 18:00 UTC)
    assert [f["lead_h"] for f in filas] == [12, 24] and filas[0]["valida"] == datetime(2026, 11, 26, 18, 0, tzinfo=UTC)
    assert abs(filas[0]["pred_c"] - 11.9) < 1e-3
    assert verif.registrar(_raw(), SLUG, datetime(2026, 11, 26, 6, 10, tzinfo=UTC)) == 4       # repetir no duplica
    with db.pool().connection() as con:
        assert con.execute("SELECT count(*) AS n FROM verif_pred").fetchone()["n"] == 4


async def test_verificar_compara_con_la_mediana_de_las_estaciones(pg, monkeypatch):
    _limpia(); ahora = datetime(2026, 11, 26, 18, 20, tzinfo=UTC)
    verif.registrar(_raw(), SLUG, datetime(2026, 11, 26, 6, 10, tzinfo=UTC))
    hora = "2026-11-26T18:00:00Z"

    async def cerca(http, lat, lon, km=40):
        return [{"temp_c": 11.0, "hora": hora, "fuente": "XEMA"}, {"temp_c": 13.0, "hora": hora, "fuente": "METAR"},
                {"temp_c": 20.0, "hora": "2026-11-26T15:00:00Z", "fuente": "XEMA"}]     # lectura antigua: se ignora
    monkeypatch.setattr(sources, "nearby", cerca)
    assert await verif.verificar(None, ahora) == 2                    # los dos modelos a 12 h; las de 24 h aún no han llegado
    with db.pool().connection() as con:
        r = con.execute("SELECT obs_c, n_est FROM verif_pred WHERE modelo = 'gfs_seamless' AND lead_h = 12").fetchone()
        assert r["obs_c"] == 12.0 and r["n_est"] == 2
        assert con.execute("SELECT count(*) AS n FROM verif_pred WHERE obs_c IS NULL").fetchone()["n"] == 2
    assert await verif.verificar(None, ahora) == 0                    # ya verificadas: no se repiten


def test_precision_ordena_por_error_y_exige_minimo(pg):
    _limpia(); ahora = datetime.now(UTC)
    with db.pool().connection() as con:
        for d in range(10):
            v = ahora - timedelta(days=d + 1)
            con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c, obs_c, n_est) VALUES (%s,'icon_seamless',24,%s,%s,10,2)", [SLUG, v, 10.5])
            con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c, obs_c, n_est) VALUES (%s,'gfs_seamless',24,%s,%s,10,2)", [SLUG, v, 12.0])
        for d in range(3):                                            # pocos datos: no se publica
            con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c, obs_c, n_est) VALUES (%s,'knmi_seamless',24,%s,10,10,2)", [SLUG, ahora - timedelta(days=d + 1)])
    r = verif.precision(SLUG)
    assert [m["modelo"] for m in r] == ["icon_seamless", "gfs_seamless"]
    assert r[0]["mae_c"] == 0.5 and r[0]["sesgo_c"] == 0.5 and r[1]["mae_c"] == 2.0 and r[0]["n"] == 10
    assert r[0]["etiqueta"] == "DWD ICON"
    _limpia()


def _siembra(maes: dict[str, float], n: int):
    """n comparaciones a 24 h por modelo, con ese error absoluto."""
    _limpia()
    ahora = datetime.now(UTC)
    with db.pool().connection() as con:
        for m, mae in maes.items():
            for i in range(n):
                con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c, obs_c) VALUES (%s, %s, 24, %s, %s, %s)",
                            [SLUG, m, ahora - timedelta(hours=6 * (i + 1)), 10.0 + mae, 10.0])


def test_pesos_apagados_por_defecto_y_exigen_historial(pg, monkeypatch):
    _siembra({"icon_seamless": 0.5, "gfs_seamless": 2.0, "ecmwf_ifs025": 1.0}, 30)
    monkeypatch.delenv("MG_PONDERAR", raising=False)
    assert verif.pesos(SLUG) is None                                   # sin activar, media simple
    monkeypatch.setenv("MG_PONDERAR", "1")
    w = verif.pesos(SLUG)
    assert w["icon_seamless"] == 2.0 and w["gfs_seamless"] == 0.5 and 0.5 < w["ecmwf_ifs025"] < 2.0   # acotados a 0,5–2
    _siembra({"icon_seamless": 0.5, "gfs_seamless": 2.0, "ecmwf_ifs025": 1.0}, 10)
    assert verif.pesos(SLUG) is None                                   # pocas comparaciones: no se mueve nada
    _limpia()


def test_media_ponderada_de_temperatura():
    from meteoguilda import openmeteo
    d = _raw()
    now = datetime(2026, 11, 25, 23, 0, tzinfo=UTC)
    simple = openmeteo.summarize(d, now=now)["steps"][0]["temperature_2m"]
    pond = openmeteo.summarize(d, now=now, pesos={"icon_seamless": 2.0, "gfs_seamless": 0.5})["steps"][0]["temperature_2m"]
    assert "ponderada" not in simple and pond["ponderada"] is True
    assert pond["mean"] > simple["mean"]                              # icon (más cálido) pesa más
    assert pond["min"] == simple["min"] and pond["max"] == simple["max"]
