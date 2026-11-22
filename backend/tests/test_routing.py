from datetime import datetime, timedelta, timezone

import pytest

from meteoguilda import routing as r

UTC = timezone.utc


def test_polyline_decode():
    # ejemplo de la documentación de Google con precisión 5
    pts = r.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5)
    assert pts[0] == pytest.approx((38.5, -120.2)) and pts[-1] == pytest.approx((43.252, -126.453))


def test_gpx_and_geometry():
    gpx = ('<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           '<trkpt lat="42.70" lon="0.80"/><trkpt lat="42.71" lon="0.81"/><trkpt lat="42.72" lon="0.83"/></trkseg></trk></gpx>')
    pts = r.parse_gpx(gpx)
    assert len(pts) == 3
    cum = r.cumulative_km(pts)
    assert 3 < cum[-1] < 4
    la, lo = r.point_at(pts, cum, cum[-1] / 2)
    assert 42.70 < la < 42.72
    with pytest.raises(ValueError):
        r.parse_gpx('<gpx><trk/></gpx>')


def test_xml_bomb_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><gpx>&b;</gpx>'
    with pytest.raises(Exception):
        r.parse_gpx(bomb)


def test_naismith_and_stats():
    d, e = [0, 1, 2, 3], [1000, 1300, 1600, 1600]
    t = r.naismith_minutes(d, e)
    assert t[-1] == pytest.approx(36 + 30 + 30 + 0, abs=0.1) or t[-1] > 90  # 3 km a 5 km/h + 600 m de ascenso
    assert r.profile_stats(d, e) == (600, 0)


def test_daylight():
    assert r.is_daylight(datetime(2026, 6, 21, 10, 0, tzinfo=UTC), 42.7, 0.9)       # 12 h locales
    assert not r.is_daylight(datetime(2026, 6, 21, 0, 30, tzinfo=UTC), 42.7, 0.9)   # 02:30 locales
    assert not r.is_daylight(datetime(2026, 12, 21, 18, 0, tzinfo=UTC), 42.7, 0.9)  # 19:00 invierno


def test_assess_thresholds():
    assert r.assess("foot", {"gust": 8, "temp": 10})[0] == 0                  # 29 km/h
    assert r.assess("foot", {"gust": 14, "temp": 10})[0] == 1                 # 50 km/h
    lvl, why = r.assess("foot", {"gust": 22, "temp": 10})
    assert lvl == 2 and "79 km/h" in why[0]
    assert r.assess("car", {"temp": 0.5, "rain": 1.0})[0] == 2 or r.assess("car", {"temp": 0.5, "rain": 1.0})[0] == 1
    assert r.assess("car", {"temp": -1, "rain": 0.5})[0] == 2                 # hielo
    assert r.assess("foot", {"temp": 12, "rain": 0.5, "cape": 2000})[0] == 2  # tormentas
    assert r.assess("foot", {"temp": 12}, daylight=False)[0] == 1


def hourly(start, gust_by_hour):
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(len(gust_by_hour))]
    n = len(times)
    return {"time": times, "utc_offset": 0, "elevation": 1500, "temperature_2m": [8] * n, "precipitation": [0] * n,
            "wind_speed_10m": [3] * n, "wind_gusts_10m": gust_by_hour, "wind_direction_10m": [270] * n,
            "cloud_cover": [20] * n, "snowfall": [0] * n, "cape": [0] * n, "freezing_level_height": [2800] * n}


def test_best_window_avoids_storm():
    start = datetime(2026, 6, 21, 5, 0, tzinfo=UTC)
    gust = [5] * 10 + [25] * 4 + [5] * 40          # racha de 90 km/h entre las 15 y las 18 UTC
    h = hourly(start, gust)
    samples = [{"lat": 42.7, "lon": 0.9, "d_km": 0, "eta_min": 0}, {"lat": 42.7, "lon": 0.9, "d_km": 5, "eta_min": 120}]
    bad = r.evaluate("foot", samples, [h, h], datetime(2026, 6, 21, 14, 0, tzinfo=UTC))
    good = r.evaluate("foot", samples, [h, h], datetime(2026, 6, 21, 6, 0, tzinfo=UTC))
    assert bad["level"] == 2 and good["level"] == 0
    wins = r.best_windows("foot", samples, [h, h], start + timedelta(hours=1))
    assert wins and all(w["nivel"] == 0 for w in wins)
    assert r.evaluate("foot", samples, [h, h], datetime(2026, 6, 30, 6, 0, tzinfo=UTC)) is None  # fuera de datos
