"""APRS en vivo (posiciones sin comprimir, comprimidas y Mic-E; última posición, 24 h) y clúster DX (caché compartida y fuentes de reserva)."""
import asyncio

import httpx
import pytest

from meteoguilda import aprs_vivo, db, main, mcp_server
from tests.test_api import api  # noqa: F401


def _b91(n, k=4):
    s = ""
    for _ in range(k):
        s = chr(n % 91 + 33) + s
        n //= 91
    return s


def test_posicion_sin_comprimir_con_rumbo_velocidad_y_altitud():
    p = aprs_vivo.parse_posicion("EA3XYZ-9>APRS,TCPIP*,qAC,T2SPAIN:!4129.54N/00221.67E>090/036/A=000328Coche de Ana")
    assert p["callsign"] == "EA3XYZ-9" and abs(p["lat"] - 41.49233) < 1e-4 and abs(p["lon"] - 2.36117) < 1e-4
    assert p["simbolo"] == "/>" and p["rumbo"] == 90 and p["velocidad_kmh"] == 67 and p["altitud_m"] == 100 and p["comentario"] == "Coche de Ana"
    assert aprs_vivo.tipo(p["simbolo"]) == "coche"
    p = aprs_vivo.parse_posicion("EB1ABC>APRS:@221830z4129.54S/00221.67W-QTH")
    assert p["lat"] < 0 and p["lon"] < 0 and p["simbolo"] == "/-" and p["comentario"] == "QTH" and p["velocidad_kmh"] is None
    assert aprs_vivo.parse_posicion("EA3XYZ>APRS::EA3ABC   :hola{1") is None                  # mensaje: sin posición
    assert aprs_vivo.parse_posicion("basura sin formato") is None


def test_posicion_comprimida():
    y, x = round((90 - 41.5) * 380926), round((180 + 2.25) * 190463)
    p = aprs_vivo.parse_posicion(f"EA3DIG>APRS:!/{_b91(y)}{_b91(x)}#  TDigi del Montseny")
    assert abs(p["lat"] - 41.5) < 1e-4 and abs(p["lon"] - 2.25) < 1e-4 and p["simbolo"] == "/#" and p["comentario"] == "Digi del Montseny"


def test_mic_e():
    # 41°29,54' N, 2°21,67' E, 36 nudos, rumbo 251, coche: destino «412YU4» (N en el 4.º, +100 en el 5.º, E en el 6.º)
    info = "`" + chr(120) + "1" + "_" + "o" + "Z" + "O" + ">" + "/" + "Kenwood"
    p = aprs_vivo.parse_posicion(f"EA3MOV-9>412YU4,WIDE1-1,qAR,EA3DIG:{info}")
    assert abs(p["lat"] - 41.49233) < 1e-4 and abs(p["lon"] - 2.36117) < 1e-4
    assert p["velocidad_kmh"] == round(36 * 1.852) and p["rumbo"] == 251 and p["simbolo"] == "/>" and p["comentario"] == "Kenwood"


def test_guardar_ultima_posicion_y_consultar(api):  # noqa: F811
    with db.pool().connection() as con:
        con.execute("TRUNCATE aprs_estaciones")
    l1 = aprs_vivo.parse_posicion("EA3XYZ-9>APRS:!4129.54N/00221.67E>")
    l2 = aprs_vivo.parse_posicion("EA3XYZ-9>APRS:!4130.00N/00222.00E>")
    l3 = aprs_vivo.parse_posicion("F4ABC>APRS:!4300.00N/00100.00E-")
    assert aprs_vivo.guardar([l1, l2, l3]) == 2                                                  # una fila por estación: la última
    r = api.get("/api/aprs?lat=41.5&lon=2.36&km=20").json()
    assert [e["indicativo"] for e in r["estaciones"]] == ["EA3XYZ-9"] and abs(r["estaciones"][0]["lat"] - 41.5) < 1e-3 and "dist_km" in r["estaciones"][0]
    r = api.get("/api/aprs?bbox=40,0,44,3").json()
    assert {e["indicativo"] for e in r["estaciones"]} == {"EA3XYZ-9", "F4ABC"} and r["estaciones"][0]["hace_min"] == 0
    with db.pool().connection() as con:
        con.execute("UPDATE aprs_estaciones SET ts = now() - interval '25 hours' WHERE callsign = 'F4ABC'")
    aprs_vivo.purgar()
    assert api.get("/api/aprs?bbox=40,0,44,3&max_age_min=1440").json()["total"] == 1
    assert api.get("/api/aprs?bbox=1,2").status_code == 422


HAMQTH = ("LW2EKY^21074.0^LU6YR^FT8 FF51 db 02^2218 2026-11-27^^E^SA^15M^Argentina^100\n"
          "UX7QV^3573.0^EA5GXY^TNX FT8 73^2218 2026-11-27^L^E^EU^80M^Spain^281\n"
          "7Q6M^7001.8^JA6WFM^CQ^2218 2026-11-27^L^E^AF^40M^Malawi^440\n")


@pytest.fixture
def dx(api, monkeypatch):  # noqa: F811
    aprs_vivo._dx.update(t=0.0, intento=0.0, spots=[], fuente=None)
    llamadas = []
    estado = {"hamqth": 200}

    def h(req):
        llamadas.append(str(req.url))
        if "hamqth" in str(req.url):
            return httpx.Response(estado["hamqth"], text=HAMQTH)
        return httpx.Response(200, json=[{"info": "CW", "dx_country": "Japan", "de_call": "SQ4INW-@", "frequency": 14025.0, "time": "2026-11-27T22:18:33", "dx_call": "JR0QFA"}])
    api.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    return api, llamadas, estado


def test_cluster_dx_filtra_y_cachea(dx):
    api, llamadas, _ = dx
    r = api.get("/api/dxcluster").json()
    assert r["fuente"] == "HamQTH" and r["total_fuente"] == 3
    s = {x["dx"]: x for x in r["spots"]}
    assert s["LU6YR"]["spotter"] == "LW2EKY" and s["LU6YR"]["banda"] == "15m" and s["LU6YR"]["modo"] == "FT8" and s["EA5GXY"]["lotw"] is True
    assert s["JA6WFM"]["modo"] == "CW" and s["JA6WFM"]["hora"] == "2026-11-27T22:18:00+00:00"           # 7001,8 kHz: segmento de CW
    assert [x["dx"] for x in api.get("/api/dxcluster?banda=40m").json()["spots"]] == ["JA6WFM"]
    assert [x["dx"] for x in api.get("/api/dxcluster?continente=eu").json()["spots"]] == ["EA5GXY"]
    assert [x["dx"] for x in api.get("/api/dxcluster?buscar=malawi").json()["spots"]] == ["JA6WFM"]
    assert len(llamadas) == 1                                                                         # caché compartida: una sola descarga


def test_cluster_dx_usa_la_reserva_y_no_insiste(dx):
    api, llamadas, estado = dx
    estado["hamqth"] = 503
    r = api.get("/api/dxcluster").json()
    assert r["fuente"] == "DX Summit" and r["spots"][0]["dx"] == "JR0QFA" and r["spots"][0]["spotter"] == "SQ4INW" and r["spots"][0]["modo"] == "CW"
    aprs_vivo._dx.update(t=0.0)                                                                       # caducada, pero hace menos de 1 min del último intento
    api.get("/api/dxcluster")
    assert len(llamadas) == 2


def test_mcp_dx_y_aprs(dx, monkeypatch):
    api, _, _ = dx
    monkeypatch.setattr(mcp_server, "_client", httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t"))
    res = str(asyncio.run(mcp_server.mcp.call_tool("get_dx_spots", {"band": "15m"})))
    assert "LU6YR" in res and "JA6WFM" not in res
    assert "estaciones" in str(asyncio.run(mcp_server.mcp.call_tool("get_aprs_stations", {"lat": 41.5, "lon": 2.36})))
