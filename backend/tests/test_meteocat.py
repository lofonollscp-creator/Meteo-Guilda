"""Predicción oficial del Meteocat: municipio más cercano, caché de 18 h, topes de cuota y fallos sin excepción."""
import json
import pathlib
from datetime import datetime, timezone

import httpx

from meteoguilda import meteocat

MUESTRA = json.loads((pathlib.Path(__file__).parent / "meteocat_municipal_080193.json").read_text())   # respuesta real del Meteocat (Barcelona)
AHORA = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _limpia(monkeypatch, key="K"):
    meteocat._mem.clear()
    almacen: dict[str, str] = {}
    monkeypatch.setattr(meteocat, "_db_get", lambda k: almacen.get(k))
    monkeypatch.setattr(meteocat, "_db_put", lambda k, v: almacen.__setitem__(k, v))
    if key:
        monkeypatch.setenv("METEOCAT_API_KEY", key)
    else:
        monkeypatch.delenv("METEOCAT_API_KEY", raising=False)
    return almacen


def _cliente(llamadas: list, status=200):
    def h(req: httpx.Request):
        llamadas.append((req.url.path, req.headers.get("x-api-key")))
        return httpx.Response(status, json=MUESTRA)
    return httpx.AsyncClient(transport=httpx.MockTransport(h))


def test_municipio_cercano():
    assert meteocat.municipio_cercano(41.3874, 2.1686)["nom"] == "Barcelona"
    assert meteocat.municipio_cercano(41.49, 2.36)["dist_km"] < 6       # Premià de Mar
    assert meteocat.municipio_cercano(40.4168, -3.7038) is None         # Madrid: fuera de Cataluña
    assert meteocat.municipio_cercano(42.0, 0.2) is None or meteocat.municipio_cercano(42.0, 0.2)["dist_km"] <= 15


def test_parsea_respuesta_real():
    d = meteocat._parsea(MUESTRA)
    assert len(d) == 8 and d[0] == {"fecha": "2026-11-26", "tmax": 16.4, "tmin": 8.3, "lluvia_prob_pct": 12.0, "cielo": "Poco o medio nuboso", "cielo_codigo": 3}


async def test_sin_clave_o_fuera_no_gasta_consulta(monkeypatch):
    _limpia(monkeypatch, key=None); ll: list = []
    async with _cliente(ll) as c:
        assert (await meteocat.prediccion(c, 41.39, 2.17, AHORA))["motivo"] == "sin_clave"
    _limpia(monkeypatch)
    async with _cliente(ll) as c:
        assert (await meteocat.prediccion(c, 40.4168, -3.7038, AHORA))["motivo"] == "fuera_de_cataluna"
    assert ll == []


async def test_cache_de_18h_y_persistencia(monkeypatch):
    alm = _limpia(monkeypatch); ll: list = []
    async with _cliente(ll) as c:
        a = await meteocat.prediccion(c, 41.3874, 2.1686, AHORA)
        assert a["disponible"] and a["municipio"] == "Barcelona" and len(a["dias"]) == 8 and not a["antiguo"]
        await meteocat.prediccion(c, 41.39, 2.17, AHORA)
        meteocat._mem.clear()                                            # «reinicio»: la base de datos sirve la predicción sin gastar consulta
        await meteocat.prediccion(c, 41.39, 2.17, AHORA)
    assert ll == [("/pronostic/v1/municipal/080193", "K")] and alm["meteocat:mes:2026-11"] == "1"


async def test_topes_de_cuota(monkeypatch):
    alm = _limpia(monkeypatch); ll: list = []
    alm["meteocat:dia:2026-11-26"] = "5"
    async with _cliente(ll) as c:
        assert (await meteocat.prediccion(c, 41.39, 2.17, AHORA))["motivo"] == "cuota"
        alm["meteocat:dia:2026-11-26"] = "0"; alm["meteocat:mes:2026-11"] = "90"
        assert (await meteocat.prediccion(c, 41.39, 2.17, AHORA))["motivo"] == "cuota"
    assert ll == []


async def test_fallo_devuelve_antiguo_o_error(monkeypatch):
    _limpia(monkeypatch); ll: list = []
    async with _cliente(ll, status=500) as c:
        assert (await meteocat.prediccion(c, 41.39, 2.17, AHORA))["motivo"] == "error"
    meteocat._mem["meteocat:pred:080193"] = (1.0, {"ts": 1.0, "dias": meteocat._parsea(MUESTRA)})     # predicción caducada
    async with _cliente(ll, status=500) as c:
        r = await meteocat.prediccion(c, 41.39, 2.17, AHORA)
    assert r["disponible"] and r["antiguo"]
