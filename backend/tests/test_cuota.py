import pytest

from meteoguilda.cuota import Cuota, CuotaAgotada

OM = "https://api.open-meteo.com/v1/forecast?latitude=1"


def test_solo_cuenta_open_meteo_y_por_servicio():
    c = Cuota()
    c.antes(OM, 1000.0)
    c.antes("https://marine-api.open-meteo.com/v1/marine?x=1", 1001.0)
    c.antes("https://example.com/x", 1002.0)                  # otro host: ni se cuenta
    e = c.estado(1010.0)
    assert e["peticiones"] == {"minuto": 2, "hora": 2, "dia": 2}
    assert e["ultima_hora_por_servicio"] == {"api/v1/forecast": 1, "marine-api/v1/marine": 1}


def test_ventanas_de_tiempo_y_purga():
    c = Cuota()
    c.antes(OM, 0.0)
    c.antes(OM, 7000.0)
    e = c.estado(7030.0)
    assert e["peticiones"] == {"minuto": 1, "hora": 1, "dia": 2}
    assert c.estado(100000.0)["peticiones"]["dia"] == 0


def test_un_429_frena_segun_el_limite_y_vuelve_a_funcionar():
    c = Cuota()
    c.antes(OM, 100.0)
    hasta = c.limite_alcanzado('{"reason":"Hourly API request limit exceeded."}', 100.0)
    assert hasta > 3000 and c.estado(200.0)["frenado"] and c.estado(200.0)["motivo"] == "por hora"
    with pytest.raises(CuotaAgotada):
        c.antes(OM, 200.0)
    assert c.frenadas == 1 and c.estado(200.0)["peticiones"]["dia"] == 1       # la evitada no cuenta como petición
    c.antes(OM, hasta + 1)                                                      # pasado el límite, se reanuda
    assert not c.estado(hasta + 2)["frenado"]


def test_limite_diario_espera_a_la_medianoche_utc_y_por_defecto_un_minuto():
    c = Cuota()
    hasta = c.limite_alcanzado("Daily API request limit exceeded", 86400 * 3 + 80000)
    assert 86400 * 4 < hasta < 86400 * 4 + 120
    c2 = Cuota()
    assert c2.limite_alcanzado("Too many", 1000.0) - 1000.0 == 65
