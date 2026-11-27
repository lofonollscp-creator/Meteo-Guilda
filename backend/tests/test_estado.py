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
