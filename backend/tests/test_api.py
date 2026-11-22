from fastapi.testclient import TestClient

from meteoguilda import main, openmeteo, store
from meteoguilda.aprs import parse_packet
from tests.test_ai import fake_openmeteo


def test_stations_and_forecast(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    con = store.connect()
    store.insert(con, parse_packet("EA1ABC>APRS:@092345z4303.50N/00541.75W_220/004g005t050h50b10132"))
    con.commit()

    async def fake_fetch(lat, lon, days=3, client=None):
        return fake_openmeteo()
    monkeypatch.setattr(openmeteo, "fetch", fake_fetch)

    with TestClient(main.app) as c:
        st = c.get("/api/stations").json()
        assert st[0]["callsign"] == "EA1ABC" and st[0]["quality"] == "ok"
        r = c.get("/api/forecast", params={"lat": 43.05, "lon": -5.7}).json()
        assert r["origen"] == "reglas"  # sin clave de OpenRouter
        assert r["datos"]["estaciones_cercanas"][0]["id"] == "EA1ABC"
        assert c.get("/").status_code == 200
