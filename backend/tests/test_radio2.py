import math
from datetime import date, datetime, timedelta, timezone

import pytest

from meteoguilda import astro, logbook, portable, radio, repeater_sources as rs, satellites
from tests.test_api import api, future_data  # noqa: F401

UTC = timezone.utc
NOW = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)


def bm(**kw):
    d = {"id": 214004, "callsign": "ED3YAU", "tx": "438.2750", "rx": "430.6750", "colorcode": 1, "lat": 42.7, "lng": 0.8,
         "city": "Val dAran", "pep": 12, "agl": 20, "last_seen": "2026-11-22 22:36:35"}
    return d | kw


def hh(**kw):
    d = {"callsign": "ED3YAU", "latitude": 42.7, "longitude": 0.8, "city": "Vielha", "group": "DMR", "mode": "DMR", "encode": "CC1",
         "frequency": 438275000, "offset": -7600000, "description": "Info https://example.org/ed3yau.", "power": "unknown", "operational": 1, "restriction": ""}
    return d | kw


def test_normalize_and_merge():
    b = rs.norm_bm(bm(), NOW)
    assert b["freq_mhz"] == 438.275 and b["shift_khz"] == -7600 and b["mode"] == "DMR" and b["band"] == "70cm" and b["dmr_id"] == 214004
    assert rs.norm_bm(bm(id=214004001), NOW) is None                      # hotspot (ID de 9 cifras)
    assert rs.norm_bm(bm(tx="430.6750", pep=1), NOW) is None              # simplex de baja potencia
    assert rs.norm_bm(bm(last_seen="2025-01-01 00:00:00"), NOW) is None   # inactivo
    assert rs.norm_bm(bm(callsign="G4ABC", lat=51.0), NOW) is None        # fuera de región
    h = rs.norm_hh(hh())
    assert h["color_code"] == 1 and h["tone_hz"] is None and h["website"] == "https://example.org/ed3yau" and h["shift_khz"] == -7600
    fm = rs.norm_hh(hh(mode="FM", encode="88.5", group="", frequency=145675000, offset=-600000))
    assert fm["tone_hz"] == 88.5 and fm["band"] == "2m" and fm["mode"] == "FM"
    assert rs.norm_hh(hh(group="IRLP")) is None and rs.norm_hh(hh(operational=0)) is None
    m = rs.merge([b], [h, fm])
    assert len(m) == 2
    dmr = next(r for r in m if r["freq_mhz"] == 438.275)
    assert dmr["source"] == "BrandMeister+HearHam" and dmr["website"] == "https://example.org/ed3yau"


def test_repeaters_db_and_filters(clean):
    rows = rs.merge([rs.norm_bm(bm(), NOW)], [rs.norm_hh(hh(mode="FM", encode="88.5", group="", frequency=145675000, offset=-600000, callsign="EA3RCI", city="Berga"))])
    assert rs.store_all(rows) == 2
    assert rs.store_all(rows) == 2                                        # idempotente
    assert len(radio.list_repeaters()) == 2
    assert [r["callsign"] for r in radio.list_repeaters(mode="DMR")] == ["ED3YAU"]
    assert [r["callsign"] for r in radio.list_repeaters(band="2m")] == ["EA3RCI"]
    assert radio.list_repeaters(q="berga")[0]["callsign"] == "EA3RCI"
    near = radio.repeaters_near(42.71, 0.81, 20, mode="DMR")
    assert near[0]["dist_km"] < 3 and near[0]["color_code"] == 1
    st = radio.repeater_stats()
    assert st["total"] == 2 and {m["mode"] for m in st["por_modo"]} == {"DMR", "FM"}
    assert radio.get_repeater(near[0]["id"])["callsign"] == "ED3YAU"
    # lo que ya no figura en las fuentes se retira, lo manual se conserva
    radio.upsert_repeater({"name": "Manual", "callsign": "EA9ZZZ", "freq_mhz": 145.5, "lat": 40, "lon": -3, "source": "manual"})
    assert rs.store_all(rows[:1]) == 1
    assert {r["callsign"] for r in radio.list_repeaters()} == {rows[0]["callsign"], "EA9ZZZ"}


def test_parse_kp_both_formats():
    assert radio.parse_kp([{"time_tag": "x", "Kp": "2.33"}]) == 2.33
    assert radio.parse_kp([["time_tag", "Kp"], ["x", "3.00"]]) == 3.0


def test_astro_known_values():
    r, t, s = astro.sun_event(40.4168, -3.7038, date(2026, 6, 21))
    assert (r.hour, abs(r.minute - 44)) == (4, 0) or abs((r.hour * 60 + r.minute) - (4 * 60 + 44)) <= 6    # 06:44 CEST
    assert abs((s.hour * 60 + s.minute) - (19 * 60 + 48)) <= 6
    _, _, set_w = astro.sun_event(40.4168, -3.7038, date(2026, 12, 21))
    assert abs((set_w.hour * 60 + set_w.minute) - (16 * 60 + 51)) <= 6
    m = astro.moon(datetime(2000, 1, 6, 18, 14, tzinfo=UTC))
    assert m["fase"] == "Luna nueva" and m["iluminacion_pct"] <= 1
    full = astro.moon(datetime(2026, 1, 3, 10, 3, tzinfo=UTC))             # luna llena real: 3 ene 2026
    assert full["iluminacion_pct"] >= 97
    assert astro.sun_moon(42.7, 0.85)["horas_de_luz"] > 8


ISS = {"name": "ISS (ZARYA)", "norad": 25544, "l1": "1 25544U 98067A   19343.69339541  .00001764  00000-0  38792-4 0  9991",
       "l2": "2 25544  51.6439 211.2001 0007417  17.6667  85.6398 15.50103472202482"}


def test_satellite_geometry_and_passes():
    from sgp4.api import Satrec, jday
    sat = Satrec.twoline2rv(ISS["l1"], ISS["l2"])
    t = datetime(2019, 12, 10, 17, 30, tzinfo=UTC)
    jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, 0)
    _, r, _ = sat.sgp4(jd, fr)
    assert 6700 < math.sqrt(sum(c * c for c in r)) < 6900                      # órbita baja
    # observador justo bajo el satélite -> elevación ~90°
    g = satellites._gmst(jd + fr)
    x, y = r[0] * math.cos(g) + r[1] * math.sin(g), -r[0] * math.sin(g) + r[1] * math.cos(g)
    lat, lon = math.degrees(math.atan2(r[2], math.hypot(x, y))), math.degrees(math.atan2(y, x))
    az, el, rng = satellites.look_angles(sat, t, lat * 0.9933, lon)           # geodésica ≈ 0,9933·geocéntrica
    assert el > 80 and 350 < rng < 450
    ps = satellites.passes(ISS, 40.4, -3.7, datetime(2019, 12, 10, 17, 0, tzinfo=UTC), 24, 10)
    assert 3 <= len(ps) <= 9 and all(p["max_el"] >= 10 and p["duracion_s"] > 60 and p["traza"] for p in ps)
    assert satellites.parse_tles("ISS\n" + ISS["l1"] + "\n" + ISS["l2"] + "\n")[0]["norad"] == 25544


def test_portable_window():
    d = future_data(gust=6.0)
    hourly = __import__("meteoguilda.openmeteo", fromlist=["x"]).hourly_consensus(d)
    out = portable.window(hourly, 42.7, 0.85)
    assert len(out["horas"]) == 48 and "aviso" in out
    stormy = future_data(gust=20.0)                                            # 72 km/h
    h2 = __import__("meteoguilda.openmeteo", fromlist=["x"]).hourly_consensus(stormy)
    assert all(r["nivel"] >= 1 for r in portable.window(h2, 42.7, 0.85)["horas"])


def test_logbook_adif_roundtrip(clean):
    key = logbook.create_profile()
    h = logbook.auth(key)
    assert h and logbook.auth("nope") is None
    q = logbook.clean({"call": "ea3xyz", "qso_at": "2026-11-22T10:15:00Z", "band": "2m", "mode": "FM", "grid": "jn02ac", "name": "Pepe", "qsl_sent": True})
    assert q["call"] == "EA3XYZ" and q["grid"] == "JN02ac"
    for bad in ({"call": "!!", "qso_at": "2026-01-01T00:00:00Z"}, {"call": "EA3XYZ", "qso_at": "x"}, {"call": "EA3XYZ", "qso_at": "2026-01-01T00:00:00Z", "grid": "ZZ"}):
        with pytest.raises(ValueError):
            logbook.clean(bad)
    ids = logbook.add_many(h, [q])
    assert logbook.update(h, ids[0], {"qsl_rcvd": True}) and not logbook.update(h, 99999, {"qsl_rcvd": True})
    rows = logbook.list_qsos(h)
    adif = logbook.to_adif(rows, "IN80do")
    assert "<CALL:6>EA3XYZ" in adif and "<QSL_RCVD:1>Y" in adif and "<MY_GRIDSQUARE:6>IN80do" in adif
    back = logbook.parse_adif(adif)
    assert back[0]["call"] == "EA3XYZ" and back[0]["qsl_rcvd"] and back[0]["qso_at"].hour == 10 and back[0]["grid"] == "JN02ac"
    st = logbook.stats(h, {"lat": 42.7, "lon": 0.85})
    assert st["total"] == 1 and st["confirmados"] == 1 and st["mas_lejano"]["km"] >= 0
    other = logbook.auth(logbook.create_profile())
    assert logbook.list_qsos(other) == [] and not logbook.delete(other, ids[0])    # aislamiento entre perfiles
    assert logbook.delete(h, ids[0])


def test_api_new_endpoints(api):  # noqa: F811
    key = api.post("/api/profile").json()["key"]
    H = {"Authorization": f"Bearer {key}"}
    assert api.get("/api/qso").status_code == 401
    r = api.post("/api/qso", headers=H, json={"call": "F4ABC", "qso_at": "2026-11-20T09:00:00Z", "band": "70cm", "mode": "DMR", "grid": "JN03"})
    assert r.status_code == 201 and r.json()["ya_trabajado"] == []
    assert api.post("/api/qso", headers=H, json={"call": "F4ABC", "qso_at": "2026-11-21T09:00:00Z"}).json()["ya_trabajado"]
    assert api.post("/api/qso", headers=H, json={"call": "??", "qso_at": "2026-11-21T09:00:00Z"}).status_code == 422
    qid = api.get("/api/qso", headers=H).json()[0]["id"]
    assert api.patch(f"/api/qso/{qid}", headers=H, json={"qsl_rcvd": True}).status_code == 200
    assert api.get("/api/qso", headers=H, params={"status": "confirmed"}).json()
    assert api.get("/api/qso/stats", headers=H).json()["total"] == 2
    assert "<CALL:5>F4ABC" in api.get("/api/qso/adif", headers=H).text
    imp = api.post("/api/qso/import", headers=H, content="<EOH>\n<CALL:6>EA3XYZ<QSO_DATE:8>20261101<TIME_ON:4>1200<BAND:3>20m<MODE:3>SSB<EOR>")
    assert imp.json() == {"importados": 1}
    assert api.put("/api/profile", headers=H, json={"qth": {"locator": "JN02ac", "lat": 42.1, "lon": 0.04}, "favs": []}).status_code == 200
    assert api.get("/api/profile", headers=H).json()["settings"]["qth"]["locator"] == "JN02ac"
    assert api.delete("/api/profile", headers=H).status_code == 204
    assert api.get("/api/qso", headers=H).status_code == 401

    a = api.get("/api/astro", params={"lat": 42.7, "lon": 0.85}).json()
    assert a["luna"]["fase"] and a["horas"] == "UTC"
    p = api.get("/api/portable", params={"lat": 42.7, "lon": 0.85}).json()
    assert p["ubicacion"]["locator"].startswith("JN0") and len(p["horas"]) == 48
    assert api.get("/api/repeaters/stats").json()["total"] == 0
    assert api.get("/api/repeaters/9999").status_code == 404
    assert api.get("/api/openapi.json").json()["info"]["title"].startswith("Meteo Guilda")


async def test_mcp_new_tools(api, monkeypatch):  # noqa: F811
    import httpx
    from meteoguilda import main, mcp_server
    tools = {t.name for t in await mcp_server.mcp.list_tools()}
    assert {"search_repeaters", "get_repeater", "repeater_stats", "satellite_passes", "sun_moon", "portable_operation_window", "get_stations",
            "logbook_create_profile", "logbook_list", "logbook_add", "logbook_mark_qsl", "logbook_stats", "logbook_export_adif",
            "logbook_import_adif"} <= tools
    async with main.app.router.lifespan_context(main.app):
        monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t"))
        key = (await mcp_server.mcp.call_tool("logbook_create_profile", {}))
        key = str(key).split("mg_")[1].split("'")[0].split('"')[0]
        key = "mg_" + key
        res = await mcp_server.mcp.call_tool("logbook_add", {"call": "EA3XYZ", "qso_at_iso": "2026-11-22T10:00:00Z", "band": "2m", "key": key})
        assert "id" in str(res)
        assert "EA3XYZ" in str(await mcp_server.mcp.call_tool("logbook_list", {"key": key}))
        assert "total" in str(await mcp_server.mcp.call_tool("logbook_stats", {"key": key}))
        assert "luna" in str(await mcp_server.mcp.call_tool("sun_moon", {"lat": 42.7, "lon": 0.85}))
        assert "horas" in str(await mcp_server.mcp.call_tool("portable_operation_window", {"lat": 42.7, "lon": 0.85}))
