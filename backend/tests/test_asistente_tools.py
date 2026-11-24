"""El asistente de IA puede buscar pueblos y usar las mismas herramientas que la app (previsión, mar, rutas, repetidores…)."""
from meteoguilda import ai, assistant_tools as at, geocode
from tests.test_api import api  # noqa: F401
from tests.test_v04 import cli  # noqa: F401


def test_parse_ignora_lo_que_no_es_una_herramienta():
    t = 'Voy a mirar.\n<tool>{"name":"prevision","args":{"lugar":"Sitges"}}</tool>\n<tool>{"name":"borrar_todo","args":{}}</tool><tool>no json</tool>'
    assert at.parse(t) == [{"name": "prevision", "args": {"lugar": "Sitges"}}]
    assert at.limpiar(t).startswith("Voy a mirar.") and "<tool>" not in at.limpiar(t)
    assert len(at.parse('<tool>{"name":"astro","args":{}}</tool>' * 5)) == at.MAX_LLAMADAS


def test_encoger_recorta_y_redondea():
    assert at.encoger({"a": 1.23456, "b": list(range(30)), "geometria": [1], "c": None}) == {"a": 1.23, "b": list(range(8))}
    assert len(at.texto({"x": "y" * 100000})) < at.MAX_CHARS + 50


def test_busca_el_pueblo_y_da_la_prevision(cli, monkeypatch):  # noqa: F811
    vistos = []

    async def comp(http, cfg, system, messages, **k):
        vistos.append((system, messages))
        if len(messages) == 1:
            return 'Miro la previsión.\n<tool>{"name":"prevision","args":{"lugar":"Vielha"}}</tool>'
        return "En Vielha (Catalunya) hará frío."
    monkeypatch.setattr(ai, "complete", comp)
    r = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿qué tiempo hará en Vielha?"}]})
    d = r.json()
    assert r.status_code == 200 and d["answer"] == "En Vielha (Catalunya) hará frío."
    assert d["herramientas"] == [{"nombre": "prevision", "etiqueta": "Previsión de modelos", "lugar": "Vielha (Catalunya, España)", "ok": True}]
    assert d["lugares"][0]["lat"] == 42.7
    t = d["tarjetas"][0]
    assert t["tipo"] == "prevision" and t["lat"] == 42.7 and t["ahora"]["temp"] is not None and len(t["serie"]) == 12 and t["dias"]
    assert "HERRAMIENTAS" in vistos[0][0]
    vuelta = vistos[1][1][-1]["content"]                                       # el modelo recibe los datos reales
    assert vuelta.startswith("Resultados de las herramientas") and '"tramos"' in vuelta and "prevision" in vuelta


def test_sin_herramientas_se_comporta_como_antes(cli, monkeypatch):  # noqa: F811
    async def comp(http, cfg, system, messages, **k):
        return "Hola."
    monkeypatch.setattr(ai, "complete", comp)
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "hola"}]}).json()
    assert d["answer"] == "Hola." and d["herramientas"] == [] and d["lugares"] == []


def test_lugar_inexistente_y_error_se_cuentan_al_modelo(cli, monkeypatch):  # noqa: F811
    async def nada(client, q, count=8, lang="es"):
        return []
    monkeypatch.setattr(geocode, "search", nada)
    vistos = []

    async def comp(http, cfg, system, messages, **k):
        vistos.append(messages)
        return '<tool>{"name":"mar","args":{"lugar":"Xyzzy"}}</tool>' if len(messages) == 1 else "No encuentro ese lugar."
    monkeypatch.setattr(ai, "complete", comp)
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "mar en Xyzzy"}]}).json()
    assert d["herramientas"][0]["ok"] is False and "No he encontrado" in d["herramientas"][0]["error"]
    assert "ERROR: No he encontrado" in vistos[1][-1]["content"]


def test_aqui_usa_el_lugar_abierto_y_hay_tope_de_vueltas(cli, monkeypatch):  # noqa: F811
    n = []

    async def comp(http, cfg, system, messages, **k):
        n.append(1)
        return 'Más.\n<tool>{"name":"astro","args":{"lugar":"aquí"}}</tool>' if len(n) < 9 else "Fin."
    monkeypatch.setattr(ai, "complete", comp)
    r = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "x"}], "lat": 42.7, "lon": 0.85, "name": "Garòs"})
    assert len(n) <= at.MAX_PASOS                                  # no entra en bucle
    assert r.status_code in (200, 502)
    if r.status_code == 200:
        assert r.json()["herramientas"][0]["lugar"] == "Garòs"


def test_ruta_y_comparar_por_nombres(cli, monkeypatch):  # noqa: F811
    async def comp(http, cfg, system, messages, **k):
        if len(messages) == 1:
            return ('<tool>{"name":"ruta","args":{"desde":"Vielha","hasta":"42.9,0.9","modo":"foot"}}</tool>\n'
                    '<tool>{"name":"comparar","args":{"lugares":["Vielha","aquí"]}}</tool>')
        return "Hecho."
    monkeypatch.setattr(ai, "complete", comp)
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "x"}], "lat": 42.5, "lon": 0.7, "name": "Aquí"}).json()
    assert [h["ok"] for h in d["herramientas"]] == [True, True] and [h["nombre"] for h in d["herramientas"]] == ["ruta", "comparar"]


def test_guardar_lugar_y_mi_ubicacion_son_propuestas_con_boton(cli, monkeypatch):  # noqa: F811
    async def comp(http, cfg, system, messages, **k):
        if len(messages) == 1:
            return ('<tool>{"name":"guardar_lugar","args":{"lugar":"Vielha"}}</tool>\n'
                    '<tool>{"name":"usar_como_mi_ubicacion","args":{"lugar":"Vielha"}}</tool>')
        return "Te he preparado los botones."
    monkeypatch.setattr(ai, "complete", comp)
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "guarda Vielha y ponla como mi ubicación"}]}).json()
    assert [t["accion"] for t in d["tarjetas"]] == ["favorito", "ubicacion"] and d["tarjetas"][0]["lat"] == 42.7
    assert cli.get("/api/me/settings").json()["home"] is None             # el servidor NO cambia nada: lo confirma la persona en la web


def test_la_tarjeta_de_prevision_trae_series_y_contraste(cli, monkeypatch):  # noqa: F811
    async def comp(http, cfg, system, messages, **k):
        return '<tool>{"name":"prevision","args":{"lugar":"Vielha"}}</tool>' if len(messages) == 1 else "Listo."
    monkeypatch.setattr(ai, "complete", comp)
    t = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "x"}]}).json()["tarjetas"][0]
    assert len(t["lluvia"]) == len(t["viento_serie"]) == len(t["horas"]) == 12 and t["modelos"] >= 1 and "contraste" in t


def test_pregunta_de_tiempo_con_lugar_abierto_crea_la_tarjeta_aunque_el_modelo_no_llame_herramientas(cli, monkeypatch):  # noqa: F811
    async def comp(http, cfg, system, messages, **k):
        return "Hace frío, 9 grados."
    monkeypatch.setattr(ai, "complete", comp)
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿Qué tiempo hace?"}], "lat": 42.7, "lon": 0.85, "name": "Garòs"}).json()
    assert [t["tipo"] for t in d["tarjetas"]] == ["prevision"] and d["tarjetas"][0]["lugar"] == "Garòs"
    assert d["herramientas"][0]["nombre"] == "prevision" and d["lugares"][0]["name"] == "Garòs"
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿Cuál es la capital de Francia?"}], "lat": 42.7, "lon": 0.85, "name": "Garòs"}).json()
    assert d["tarjetas"] == []                                           # fuera del tiempo: sin tarjeta
    cli.put("/api/me/settings", json={"home": {"lat": 41.6, "lon": 0.6, "name": "Lleida"}})
    d = cli.post("/api/assistant", json={"messages": [{"role": "user", "content": "¿Qué tiempo hace aquí?"}], "lat": 42.7, "lon": 0.85, "name": "Garòs"}).json()
    assert d["tarjetas"][0]["lugar"] == "Lleida"                         # «aquí» = Mi ubicación
    cli.put("/api/me/settings", json={"home": None})


def test_tarjetas_comparar_mar_y_satelites():
    c = at.tarjeta_comparar({"lugares": [{"nombre": "A", "lat": 1, "lon": 2, "ahora_c": 10, "min_c": 8, "max_c": 15, "lluvia_48h_mm": 0, "racha_max_kmh": 20, "series": {"x": 1}},
                                         {"nombre": "B", "lat": 1, "lon": 3, "ahora_c": 11, "min_c": 9, "max_c": 17, "lluvia_48h_mm": 3, "racha_max_kmh": 30}], "veredicto": ["B es más cálido"] * 5})
    assert c["tipo"] == "comparar" and len(c["lugares"]) == 2 and "series" not in c["lugares"][0] and len(c["veredicto"]) == 3
    assert at.tarjeta_comparar({"lugares": [{"nombre": "A"}]}) is None
    p = {"name": "Sitges", "lat": 41.2, "lon": 1.8}
    assert at.tarjeta_mar(p, {"hay_mar": False}) is None
    horas = [{"hora": f"2026-11-24T{h % 24:02d}:00", "ola_m": 0.5 + h / 10} for h in range(48)]
    m = at.tarjeta_mar(p, {"hay_mar": True, "ahora": {"ola_m": 0.5}, "maximo_48h": {"ola_m": 5.2}, "horas": horas})
    assert m["tipo"] == "mar" and len(m["serie"]) == 16 and m["lugar"] == "Sitges"
    s = at.tarjeta_satelites(p, {"pasos": [{"satelite": "ISS", "aos": "2026-11-24T20:00:00+00:00", "aos_az": 10, "max_el": 55, "duracion_s": 400, "traza": [[1, 2]]}] * 6})
    assert s["tipo"] == "satelites" and len(s["pasos"]) == 4 and "traza" not in s["pasos"][0]
    assert at.tarjeta_satelites(p, {"pasos": []}) is None
