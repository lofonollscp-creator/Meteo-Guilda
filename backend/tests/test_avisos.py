from datetime import datetime, timezone

from meteoguilda import avisos

CAP = """<?xml version="1.0"?><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2"><identifier>x</identifier>
<info><language>es-ES</language><event>Aviso de lluvia de nivel naranja</event><severity>Severe</severity><onset>2026-11-23T10:00:00+01:00</onset><expires>2099-01-01T00:00:00+01:00</expires>
<headline>Lluvia. Pirineo de Lleida</headline><description>Acumulados de 60 mm.</description><instruction>Evite cauces.</instruction>
<area><areaDesc>Pirineo de Lleida</areaDesc><polygon>42.5,0.5 42.5,1.2 42.9,1.2 42.9,0.5 42.5,0.5</polygon></area>
<area><areaDesc>Otra zona</areaDesc><polygon>40,-3 40,-2 41,-2 41,-3 40,-3</polygon></area></info>
<info><language>en-GB</language><event>Orange rain warning</event><severity>Severe</severity><expires>2099-01-01T00:00:00+01:00</expires>
<area><areaDesc>Pirineo de Lleida</areaDesc><polygon>42.5,0.5 42.5,1.2 42.9,1.2 42.9,0.5 42.5,0.5</polygon></area></info></alert>"""


def test_el_cap_se_lee_con_el_texto_en_espanol_y_sin_duplicar_zonas():
    a = avisos.parse_cap(CAP)
    assert len(a) == 3 and a[0]["evento"].startswith("Aviso de lluvia") and a[0]["nivel"] == 2     # la zona en inglés es otra clave de evento
    assert a[0]["poligonos"][0][0] == (42.5, 0.5)


def test_solo_salen_los_avisos_cuyo_poligono_contiene_el_punto():
    todos = avisos.parse_cap(CAP)
    garos = avisos.para_punto(todos, 42.7, 0.85)
    assert garos["disponible"] and {a["zona"] for a in garos["avisos"]} == {"Pirineo de Lleida"} and garos["avisos"][0]["color"] == "naranja"
    assert "poligonos" not in garos["avisos"][0]
    assert avisos.para_punto(todos, 38.0, -1.0)["avisos"] == []


def test_sin_clave_no_se_inventa_nada_y_los_enlaces_dependen_del_lugar():
    r = avisos.para_punto(None, 42.7, 0.85)
    assert r["disponible"] is False and r["avisos"] == []
    nombres = [e["nombre"] for e in r["enlaces"]]
    assert "AEMET" in nombres and "Meteocat" in nombres and nombres[-1].startswith("MeteoAlarm")
    assert "Meteocat" not in [e["nombre"] for e in avisos.para_punto(None, 37.4, -5.9)["enlaces"]]


def test_un_aviso_caducado_no_es_vigente():
    a = {"hasta": "2020-01-01T00:00:00+01:00"}
    assert not avisos.vigente(a, datetime.now(timezone.utc)) and avisos.vigente({"hasta": None}, datetime.now(timezone.utc))


def test_fallo_de_aemet_no_se_reintenta_en_cada_visita(monkeypatch):
    """Un 429 de AEMET no debe repetirse en cada petición: se sirve lo último bueno y se espera antes de reintentar."""
    import asyncio
    import time
    import httpx
    monkeypatch.setenv("AEMET_API_KEY", "x")
    llamadas = []

    async def mal(client, key):
        llamadas.append(1)
        raise httpx.HTTPStatusError("429", request=httpx.Request("GET", "https://x"), response=httpx.Response(429, headers={"retry-after": "120"}))

    monkeypatch.setattr(avisos, "_bajar", mal)
    monkeypatch.setattr(avisos, "_lock", None)
    monkeypatch.setattr(avisos, "_hasta", 0.0)
    monkeypatch.setattr(avisos, "_cache", (0, []))
    assert asyncio.run(avisos.todos(None)) is None          # nunca hubo datos: «no disponible»
    monkeypatch.setattr(avisos, "_lock", None)              # un lock nuevo por cada asyncio.run de la prueba
    assert asyncio.run(avisos.todos(None)) is None
    assert len(llamadas) == 1                                 # el segundo intento ni llama a AEMET
    assert 100 < avisos._hasta - time.time() <= 120          # respeta Retry-After
    bueno = [{"id": "a"}]                                     # con una lista buena guardada, se sigue sirviendo aunque falle
    monkeypatch.setattr(avisos, "_cache", (time.time() - avisos.TTL - 1, bueno))
    monkeypatch.setattr(avisos, "_hasta", 0.0)
    monkeypatch.setattr(avisos, "_lock", None)
    assert asyncio.run(avisos.todos(None)) == bueno
