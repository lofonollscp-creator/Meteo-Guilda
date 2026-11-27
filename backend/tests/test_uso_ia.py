"""Tope diario del asistente con la IA del servidor."""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from meteoguilda import uso_ia, vigilante
from tests.test_api import api  # noqa: F401


def test_quien_paga(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "clave-servidor")
    assert uso_ia.es_del_servidor(None) and uso_ia.es_del_servidor("clave-servidor")
    assert not uso_ia.es_del_servidor("clave-de-la-persona")
    monkeypatch.delenv("MISTRAL_API_KEY")
    assert not uso_ia.es_del_servidor("clave-de-la-persona")


def test_tope_diario_y_clave_propia_sin_tope(monkeypatch, api):  # noqa: F811
    monkeypatch.setenv("MG_ASISTENTE_DIA", "3")
    hoy = datetime(2031, 1, 5, 12, tzinfo=timezone.utc)
    for _ in range(3):
        uso_ia.comprobar("persona-a", None, hoy)
    with pytest.raises(HTTPException) as e:
        uso_ia.comprobar("persona-a", None, hoy)
    assert e.value.status_code == 429 and "3 consultas" in e.value.detail
    uso_ia.comprobar("persona-b", None, hoy)                       # otra persona: su propio contador
    for _ in range(10):
        uso_ia.comprobar("persona-a", "clave-propia", hoy)         # con clave propia no cuenta ni se limita
    r = uso_ia.resumen(hoy)
    assert r["personas_hoy"] == 2 and r["consultas_hoy"] == 5 and r["maximo_por_persona"] == 4 and r["tope_por_persona"] == 3
    assert "persona-a" not in str(r)


def test_tope_cero_es_sin_tope(monkeypatch, api):  # noqa: F811
    monkeypatch.setenv("MG_ASISTENTE_DIA", "0")
    hoy = datetime(2031, 1, 6, 12, tzinfo=timezone.utc)
    for _ in range(5):
        uso_ia.comprobar("persona-c", None, hoy)


def test_el_contador_de_ayer_se_borra(monkeypatch, api):  # noqa: F811
    monkeypatch.setenv("MG_ASISTENTE_DIA", "100")
    uso_ia.anota("persona-d", datetime(2031, 2, 1, 12, tzinfo=timezone.utc))
    uso_ia.anota("persona-d", datetime(2031, 2, 2, 12, tzinfo=timezone.utc))
    assert uso_ia.resumen(datetime(2031, 2, 1, 12, tzinfo=timezone.utc))["consultas_hoy"] == 0


def test_alerta_baja_no_cambia_el_semaforo():
    est = {"aprs": {"ultimo_paquete_hace_s": 5}, "asistente": {"tope_por_persona": 100, "maximo_por_persona": 100}}
    a = vigilante.alertas(est, config=[])
    assert a and a[0]["nivel"] == "bajo" and vigilante.semaforo(a) == "verde"
