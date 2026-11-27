"""Planificador de salidas por tramos: reglas por perfil (monte, carretera, mar), semáforo, ventana y equipo."""
from meteoguilda import mar as mar_mod
from meteoguilda import salidas
from tests.test_api import api, future_data  # noqa: F401
from tests.test_v04 import cli  # noqa: F401


def test_reglas_de_monte():
    m = {"temp_c": -2, "viento_kmh": 30, "racha_kmh": 50, "lluvia_mm": 0.5, "nieve_cm": 0.3, "isocero_m": 1600, "cape_jkg": 0, "nubes_pct": 100, "humedad_pct": 98}
    lv, why = salidas.reglas_extra("monte", m, 1800, 10, False)
    assert lv == 2 and any("Cota de nieve (1600 m)" in w for w in why) and any("Niebla" in w for w in why)
    lv, why = salidas.reglas_extra("monte", {**m, "lluvia_mm": 0, "nieve_cm": 0, "temp_c": 5, "viento_kmh": 5}, 800, 10, False)
    assert lv == 0 and why == []
    lv, why = salidas.reglas_extra("monte", {"temp_c": 20, "cape_jkg": 1500}, 1200, 16, False)
    assert lv == 2 and "Tormentas" in why[0]
    assert salidas.reglas_extra("monte", {"temp_c": 20, "cape_jkg": 1500}, 1200, 9, False)[0] == 0          # por la mañana no
    assert salidas.sensacion(-5, 40) < -12 and salidas.sensacion(15, 40) == 15


def test_reglas_de_carretera_y_mar():
    lv, why = salidas.reglas_extra("carretera", {"temp_c": 1, "humedad_pct": 99, "viento_kmh": 3, "racha_kmh": 75, "lluvia_mm": 0}, None, 7, False)
    assert lv == 1 and len(why) == 3
    lv, why = salidas._nivel_mar({"ola_m": 2.7, "viento_kmh": 40, "racha_kmh": 55}, False)
    assert lv == 2 and "gruesa" not in why[0] and "fuerte marejada" in why[0] and any("nudos" in w for w in why)
    assert salidas._nivel_mar({"ola_m": 0.4, "viento_kmh": 10, "racha_kmh": 15}, False) == (0, [])
    assert salidas._nivel_mar({"ola_m": 0.4}, True)[0] == 1


def test_ruta_incluye_semaforo_por_tramos(api):  # noqa: F811
    r = api.post("/api/route", json={"waypoints": [{"lat": 42.7, "lon": 0.8}, {"lat": 42.75, "lon": 0.9}], "mode": "foot"}).json()
    s = r["salida_por_tramos"]
    assert s["disponible"] and s["perfil"] == "monte" and len(s["tramos"]) == len(r["muestras"]) - 1
    assert s["tramos"][0]["color"] in ("verde", "ámbar", "rojo") and s["mejor_ventana"] and "Agua y algo de comida" in s["equipo"]


def test_salida_en_monte_con_ruta_guardada(cli):  # noqa: F811
    rid = cli.post("/api/rutas", json={"nombre": "Coche al valle", "modo": "car", "waypoints": [{"lat": 42.7, "lon": 0.8}, {"lat": 42.9, "lon": 1.0}]}).json()["id"]
    r = cli.post("/api/salidas", json={"ruta_id": rid}).json()
    assert r["perfil"] == "carretera" and r["ruta"] == "Coche al valle" and r["tramos"] and "Depósito lleno y teléfono cargado" in r["equipo"]
    assert cli.post("/api/salidas", json={"ruta_id": 999999}).status_code == 404
    assert cli.post("/api/salidas", json={"waypoints": [{"lat": 42.7, "lon": 0.8}]}).status_code == 422


def test_salida_en_mar(api, monkeypatch):  # noqa: F811
    async def mar(http, lat, lon):
        d = future_data(lat, lon)
        horas = [{"hora": t, "ola_m": 1.8 if lon > 2.6 else 0.6} for t in d["hourly"]["time"]]
        return {"hay_mar": True, "horas": horas}
    monkeypatch.setattr(mar_mod, "fetch", mar)
    r = api.post("/api/salidas", json={"perfil": "mar", "waypoints": [{"lat": 41.3, "lon": 2.2}, {"lat": 41.3, "lon": 2.9}], "nudos": 6}).json()
    assert r["perfil"] == "mar" and r["distancia_mn"] > 25 and r["velocidad_nudos"] == 6
    assert r["tramos"][0]["nivel"] <= r["tramos"][-1]["nivel"] and any("Oleaje de 1.8 m" in m for t in r["tramos"] for m in t["motivos"])
    assert "Chaleco salvavidas para cada persona" in r["equipo"] and "Ropa de agua" in r["equipo"]
    assert len(r["ventanas"]) == 3 and r["mejor_ventana"]
