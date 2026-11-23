from meteoguilda import comparar


def _s(temp, lluvia, racha, elev=100):
    steps = [{"time": f"2026-11-23T{(i * 3) % 24:02d}:00", "temperature_2m": {"mean": temp, "min": temp - 1, "max": temp + 1}, "precipitation": {"mean": lluvia},
              "wind_gusts_10m": {"max": racha}, "wind_speed_10m": {"mean": racha / 2}, "cloud_cover": {"mean": 50}} for i in range(16)]
    return {"steps": steps, "elevation_m": elev}


def test_fila_convierte_unidades_y_acumula():
    f = comparar.fila("A", 41.0, 2.0, _s(10, 0.5, 10))
    assert f["racha_max_kmh"] == 36 and f["lluvia_48h_mm"] == 8.0 and f["ahora_c"] == 10 and f["min_c"] == 9 and f["max_c"] == 11 and len(f["tiempo"]) == 16


def test_veredicto_no_inventa_diferencias():
    a, b = comparar.fila("Costa", 41, 2, _s(15, 0.0, 5)), comparar.fila("Cima", 42, 1, _s(14, 0.0, 5))
    v = comparar.veredicto([a, b])
    assert not any("más calor" in x or "más viento" in x or "mejor" in x for x in v)


def test_veredicto_con_diferencias_claras():
    a, b = comparar.fila("Costa", 41, 2, _s(16, 0.0, 5)), comparar.fila("Cima", 42, 1, _s(4, 1.0, 20))
    v = " ".join(comparar.veredicto([a, b]))
    assert "más calor en Costa" in v and "más lluvia" in v and "Cima" in v and "mejor" in v and "Costa" in v.split("mejor")[1][:40]
