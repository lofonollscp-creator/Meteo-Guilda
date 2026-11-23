"""Contraste modelos vs. estaciones reales y modelos adicionales."""
from meteoguilda import ai, contraste, openmeteo


def _ctx(estaciones, t=15.0, v=10):
    return {"tramos": [{"time": "2026-11-24T12:00", "confianza": "alta", "temp_c": {"mean": t}, "viento_kmh": {"mean": v}} for _ in range(4)],
            "estaciones_cercanas": estaciones}


def test_estaciones_que_coinciden_no_cambian_nada():
    c = _ctx([{"temp_c": 15.5, "viento_kmh": 12, "dist_km": 5, "fuente": "APRS"}, {"temp_c": 14.8, "viento_kmh": 9, "dist_km": 12, "fuente": "XEMA"}])
    contraste.aplicar(c)
    assert c["contraste"]["acuerdo"] == "alto" and c["contraste"]["estaciones"] == 2 and c["contraste"]["fuentes"] == ["APRS", "XEMA"]
    assert [t["confianza"] for t in c["tramos"]] == ["alta"] * 4


def test_estaciones_que_discrepan_bajan_la_confianza_de_las_primeras_horas():
    c = _ctx([{"temp_c": 21.0, "viento_kmh": 10, "dist_km": 4, "fuente": "METAR"}])
    contraste.aplicar(c)
    assert c["contraste"]["temp"]["dif"] == 6.0 and c["contraste"]["acuerdo"] == "bajo"
    assert [t["confianza"] for t in c["tramos"]] == ["baja", "baja", "baja", "alta"]      # solo las 3 primeras (9 h)


def test_estaciones_lejanas_o_sin_estaciones_se_ignoran():
    assert contraste.contrastar(_ctx([])) is None
    assert contraste.contrastar(_ctx([{"temp_c": 30, "dist_km": 90}])) is None
    c = _ctx([])
    assert contraste.aplicar(c) is c and "contraste" not in c


def test_build_context_incluye_el_contraste_y_la_ia_lo_ve():
    from tests.test_ai import fake_openmeteo
    from datetime import datetime, timezone
    s = openmeteo.summarize(fake_openmeteo(), now=datetime(2026, 11, 22, 0, 0, tzinfo=timezone.utc))
    ctx = ai.build_context(s, [{"id": "X", "fuente": "APRS", "dist_km": 3, "temp_c": 30.0, "viento_kmh": 5}])
    assert ctx["contraste"]["acuerdo"] == "bajo" and "contraste" in ai.ai_view(ctx)


def test_hay_modelos_regionales_y_todos_tienen_nombre():
    assert len(openmeteo.MODELS) == len(set(openmeteo.MODELS)) >= 13
    assert all(m in openmeteo.MODEL_LABELS for m in openmeteo.MODELS)
