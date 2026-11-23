from datetime import datetime, timezone

from meteoguilda import mar

NOW = datetime(2026, 11, 23, 10, 5, tzinfo=timezone.utc)


def _d(waves):
    t = [f"2026-11-23T{h:02d}:00" for h in range(24)] + [f"2026-11-24T{h:02d}:00" for h in range(24)]
    n = len(t)
    return {"utc_offset_seconds": 0, "hourly": {"time": t, "wave_height": (waves + [None] * n)[:n], "wave_period": [6.0] * n, "wave_direction": [90] * n,
                                                 "swell_wave_height": [0.5] * n, "swell_wave_period": [8.0] * n, "sea_surface_temperature": [15.0] * n}}


def test_tierra_adentro_no_inventa_mar():
    r = mar.summarize(_d([None] * 48), 40.4, -3.7, NOW)
    assert r["hay_mar"] is False and r["horas"] == []


def test_estado_douglas_y_maximo_de_48h():
    w = [0.3] * 10 + [1.0, 2.0, 3.2] + [0.8] * 35
    r = mar.summarize(_d(w), 41.3, 2.2, NOW)
    assert r["hay_mar"] and r["ahora"]["estado"] == "marejadilla"
    assert r["maximo_48h"]["ola_m"] == 3.2 and r["maximo_48h"]["estado"] == "fuerte marejada" and r["maximo_48h"]["nivel"] == 2
    assert r["horas"][0]["hora"] == "2026-11-23T10:00"


def test_escala():
    assert mar.estado(0.05)[0] == "calma" and mar.estado(1.0)[1] == 0 and mar.estado(5.0)[1] == 2
