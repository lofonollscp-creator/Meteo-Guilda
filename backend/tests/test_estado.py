"""Panel de salud de administración."""
from tests.test_api import api  # noqa: F401
from tests.test_v04 import cli  # noqa: F401


def test_admin_estado_exige_token_y_solo_da_agregados(cli, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MG_ADMIN_TOKEN", "t0k")
    assert cli.get("/api/admin/estado").status_code == 403
    assert cli.get("/api/admin/estado", headers={"x-admin-token": "mala"}).status_code == 403
    r = cli.get("/api/admin/estado", headers={"x-admin-token": "t0k"})
    d = r.json()
    assert r.status_code == 200 and set(d) >= {"claves", "aprs", "avisos_aemet", "meteocat", "verificacion", "push"}
    assert d["meteocat"]["tope_mes"] == 90 and all(isinstance(v, bool) for v in d["claves"].values())
    assert "sub" not in str(d).lower() or "endpoint" not in str(d).lower()      # nada de personas ni de dispositivos concretos


# ---- configuración y vigilante ----
from datetime import datetime, timezone  # noqa: E402

from meteoguilda import configuracion, vigilante  # noqa: E402


def test_config_detecta_comentario_pegado_al_valor():
    av = configuracion.avisos({"MG_APRS_FILTER": "   # vacío = círculos", "MG_OTRA": "ok", "HOME": "# no es nuestra"})
    assert any(a.startswith("MG_APRS_FILTER") and "comentario" in a for a in av)
    assert not any(a.startswith(("MG_OTRA", "HOME")) for a in av)


def test_config_no_muestra_nunca_el_valor():
    av = configuracion.avisos({"MG_ADMIN_TOKEN": "supersecreto # y más"})
    assert av and "supersecreto" not in " ".join(av)


def test_config_valida_el_filtro_aprs():
    assert configuracion.avisos({"MG_APRS_FILTER": "r/41.490/2.360/60 r/41.40/2.17/30"}) == []
    assert configuracion.avisos({"MG_APRS_FILTER": ""}) == []
    assert configuracion.avisos({"MG_APRS_FILTER": "vacio"})
    assert configuracion.avisos({"MG_APRS_FILTER": "r/999/2/60"})
    assert configuracion.avisos({"MG_APRS_PORT": "abc"})


def test_vigilante_alerta_si_aprs_calla_o_nunca_entro():
    ok = {"aprs": {"ultimo_paquete_hace_s": 120}}
    assert vigilante.alertas(ok, config=[]) == []
    assert vigilante.alertas({"aprs": {"ultimo_paquete_hace_s": 4 * 3600}}, config=[])[0]["id"] == "aprs"
    assert vigilante.alertas({"aprs": {"ultimo_paquete_hace_s": None}}, config=[])[0]["nivel"] == "alto"
    assert vigilante.alertas(ok, config=["MG_X: mal"])[0]["texto"].startswith("Configuración")


def test_vigilante_sin_cuentas_no_encola(monkeypatch):
    monkeypatch.delenv("MG_ADMIN_SUBS", raising=False)
    assert vigilante.avisar_admin([{"id": "aprs", "nivel": "alto", "texto": "x"}], datetime.now(timezone.utc)) == 0


def test_estado_incluye_alertas(cli, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MG_ADMIN_TOKEN", "t0k")
    d = cli.get("/api/admin/estado", headers={"x-admin-token": "t0k"}).json()
    assert isinstance(d["alertas"], list)
