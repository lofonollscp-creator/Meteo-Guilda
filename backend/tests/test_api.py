import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from meteoguilda import geocode, main, openmeteo, routing, store
from tests.test_store import pk


def future_data(lat=42.7, lon=0.9, hours=96, gust=8.0):
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    t = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    h = {"time": t}
    for m in ("ecmwf_ifs025", "icon_seamless"):
        for v, val in [("temperature_2m", 9.0), ("precipitation", 0.1), ("wind_speed_10m", 3.0), ("wind_gusts_10m", gust),
                       ("wind_direction_10m", 270), ("cloud_cover", 40), ("relative_humidity_2m", 70),
                       ("surface_pressure", 1010), ("snowfall", 0), ("cape", 0), ("freezing_level_height", 2800)]:
            h[f"{v}_{m}"] = [val] * hours
    return {"latitude": lat, "longitude": lon, "elevation": 1300, "utc_offset_seconds": 0, "hourly": h}


@pytest.fixture
def api(clean, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    main._summaries.clear(); main._forecasts.clear(); main._hits.clear(); main._ai.clear()

    async def no_sources(http, lat, lon, km=40):   # sin red: METAR/XEMA se prueban aparte en test_v04
        return []
    monkeypatch.setattr(main.sources, "nearby", no_sources)

    async def fetch(lat, lon, days=5, client=None):
        return future_data(lat, lon)

    async def fetch_many(points, days=3, client=None):
        return [future_data(la, lo) for la, lo in points]

    async def elevations(points, client):
        return [1000 + 50 * i for i in range(len(points))]

    async def valhalla(wps, mode, client):
        a, b = wps[0], wps[-1]
        return [(a[0] + (b[0] - a[0]) * i / 20, a[1] + (b[1] - a[1]) * i / 20) for i in range(21)], 3600

    async def gsearch(client, q, count=8, lang="es"):
        return [{"name": "Vielha", "admin1": "Catalunya", "country": "España", "cc": "ES", "lat": 42.7, "lon": 0.79, "ele": 971}]

    monkeypatch.setattr(openmeteo, "fetch", fetch)
    monkeypatch.setattr(openmeteo, "fetch_many", fetch_many)
    monkeypatch.setattr(openmeteo, "elevations", elevations)
    monkeypatch.setattr(routing, "valhalla_route", valhalla)
    monkeypatch.setattr(routing, "is_daylight", lambda *a, **k: True)
    monkeypatch.setattr(geocode, "search", gsearch)
    store.insert_many([pk("EA1ABC", 43.0, -5.7), pk("EA3GAR", 42.72, 0.87)])
    with TestClient(main.app) as c:
        yield c


def test_places_forecast_search(api):
    pl = api.get("/api/places").json()
    assert len(pl) == 6 and pl[0]["slug"] == "garos" and pl[0]["now"]["temp_c"] == 9.0
    g = api.get("/api/forecast", params={"place": "garos"}).json()
    assert g["origen"] == "reglas" and g["lugar"]["name"] == "Garòs"
    assert g["datos"]["estaciones_cercanas"][0]["id"] == "EA3GAR"
    assert len(g["datos"]["dias"]) >= 3 and g["datos"]["tramos"][0]["viento_dir_txt"] == "O"
    free = api.get("/api/forecast", params={"lat": 42.7, "lon": 0.79, "name": "Vielha"}).json()
    assert free["lugar"]["name"] == "Vielha"
    assert api.get("/api/forecast", params={"place": "nope"}).status_code == 404
    assert api.get("/api/search", params={"q": "viel"}).json()[0]["name"] == "Vielha"
    snap = api.get("/api/snapshot", params={"pts": "42.7,0.79;41.5,2.3"}).json()
    assert len(snap) == 2 and snap[0]["now"]["temp_c"] == 9.0
    raw = api.get("/api/raw", params={"lat": 42.7, "lon": 0.8}).json()
    assert "pronostico" not in raw and raw["datos"]["tramos"]
    assert api.get("/api/health").json() == {"ok": True, "db": True}
    assert api.get("/").status_code == 200


def test_stations_and_radio(api):
    st = api.get("/api/stations").json()
    assert {s["callsign"] for s in st} == {"EA1ABC", "EA3GAR"} and all(s["quality"] == "ok" for s in st)
    assert api.get("/api/locator", params={"lat": 48.14666, "lon": 11.60833}).json()["locator"] == "JN58td"
    assert api.get("/api/locator", params={"loc": "bad!"}).status_code == 422
    assert api.get("/api/repeaters").json() == []
    assert api.post("/api/admin/repeaters", json={"name": "x", "freq_mhz": 145.6, "lat": 42, "lon": 1}).status_code == 403


def test_route_foot_and_car(api):
    body = {"waypoints": [{"lat": 42.70, "lon": 0.80}, {"lat": 42.75, "lon": 0.90}], "mode": "foot"}
    r = api.post("/api/route", json=body).json()
    assert r["modo"] == "foot" and r["distancia_km"] > 8 and r["ascenso_m"] > 0 and len(r["muestras"]) >= 4
    assert r["nivel_global"] == 0 and r["ventanas"] and r["perfil"] and r["geometria"]
    assert r["muestras"][0]["meteo"]["viento_kmh"] == 11 and r["lectura"] is None
    assert any(b["id"] == "EA3GAR" for b in r["balizas"])
    stormy = future_data(gust=25.0)

    async def many(points, days=3, client=None):
        return [stormy for _ in points]
    openmeteo.fetch_many = many
    r2 = api.post("/api/route", json={**body, "mode": "car"}).json()
    assert r2["nivel_global"] == 1 or r2["nivel_global"] == 2
    assert api.post("/api/route", json={"waypoints": [{"lat": 1, "lon": 1}]}).status_code == 422
    gpx = ('<gpx><trk><trkseg><trkpt lat="42.70" lon="0.80"/><trkpt lat="42.72" lon="0.84"/></trkseg></trk></gpx>')
    assert api.post("/api/route", json={"gpx": gpx, "mode": "foot"}).status_code == 200
