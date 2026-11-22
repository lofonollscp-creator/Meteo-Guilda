import pytest

from meteoguilda import radio as rd


def test_maidenhead():
    assert rd.to_locator(48.14666, 11.60833) == "JN58td"
    la, lo = rd.from_locator("JN58td")
    assert la == pytest.approx(48.146, abs=0.03) and lo == pytest.approx(11.625, abs=0.06)
    assert rd.to_locator(*rd.from_locator("IN92")) .startswith("IN92")
    with pytest.raises(ValueError):
        rd.from_locator("??")


def test_refractivity_and_classes():
    n = rd.refractivity(1013, 15, 50)
    assert 300 < n < 330
    assert rd.classify_gradient(-40) == "normal" and rd.classify_gradient(-100) == "super-refracción"
    assert rd.classify_gradient(-200) == "conducto" and rd.classify_gradient(10) == "sub-refracción"


def test_propagation_series_duct():
    t = ["2026-06-21T10:00", "2026-06-21T11:00"]
    d = {"elevation": 20, "hourly": {"time": t, "surface_pressure": [1015, 1015], "temperature_2m": [20, 14],
         "relative_humidity_2m": [100, 90], "temperature_850hPa": [22, 8], "relative_humidity_850hPa": [10, 60],
         "geopotential_height_850hPa": [1500, 1500], "temperature_700hPa": [0, 0], "relative_humidity_700hPa": [50, 50],
         "geopotential_height_700hPa": [3000, 3000]}}
    from datetime import datetime, timezone
    s = rd.propagation_series(d, now=datetime(2026, 6, 21, 9, 0, tzinfo=timezone.utc))
    assert s["serie"][0]["cat"] in ("conducto", "super-refracción")      # inversión seca en altura
    assert s["serie"][1]["cat"] in ("normal", "super-refracción")
    assert s["peor"]["gradient"] == s["serie"][0]["gradient"]


def test_radial_limit_flat_and_blocked():
    dists = [float(i) for i in range(1, 41)]
    flat = rd.radial_limit(0, 10, dists, [0.0] * 40)
    assert 14 <= flat <= 22                                  # horizonte radio ≈ 18 km
    ridge = [0.0] * 40
    ridge[4] = 400                                           # cresta a 5 km
    assert rd.radial_limit(0, 10, dists, ridge) <= 5
    hill_top = rd.radial_limit(500, 10, dists, [0.0] * 40)   # repetidor en alto domina el valle
    assert hill_top > flat


def test_repeaters_db(clean):
    rid = rd.upsert_repeater({"name": "R-Test", "callsign": "EA3RTX", "band": "2m", "freq_mhz": 145.6, "shift_khz": -600,
                              "tone_hz": 123.0, "lat": 42.70, "lon": 0.86})
    assert rd.upsert_repeater({"name": "R-Test2", "callsign": "EA3RTX", "band": "2m", "freq_mhz": 145.6, "lat": 42.70,
                               "lon": 0.86}) == rid                       # upsert idempotente
    ring = rd.coverage_ring(42.70, 0.86, [10.0] * 36)
    wkt = "POLYGON((" + ",".join(f"{lo} {la}" for la, lo in ring) + "))"
    from meteoguilda.db import pool
    with pool().connection() as con:
        con.execute("UPDATE repeaters SET coverage = ST_GeogFromText(%s) WHERE id = %s", [wkt, rid])
    near = rd.repeaters_near(42.72, 0.88, 30)
    assert near[0]["callsign"] == "EA3RTX" and near[0]["covers_point"] is True
    assert rd.repeaters_near(41.5, 2.3, 30) == []
    along = rd.repeaters_along([(42.60, 0.86), (42.80, 0.86)])           # ruta que atraviesa el círculo de 10 km
    assert along and 85 < along[0]["pct"] < 95                           # ≈ 20 km de 22 km
    assert rd.list_repeaters(with_coverage=True)[0]["coverage_geojson"]["type"] == "Polygon"
