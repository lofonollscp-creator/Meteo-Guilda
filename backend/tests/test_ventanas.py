from datetime import datetime, timedelta, timezone

from meteoguilda import ventanas


def _ahora():
    return datetime(2026, 11, 23, 10, 20, tzinfo=timezone.utc)


def _horas_campo(nivel=0, motivos=()):
    ini = datetime(2026, 11, 23, 11, tzinfo=timezone.utc)
    return [{"t": (ini + timedelta(hours=i)).isoformat(), "nivel": nivel, "motivos": list(motivos), "racha_kmh": 20, "lluvia_mm": 0.0} for i in range(48)]


def _tropo(cat="normal", g=-40):
    ini = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)      # hora local = UTC+1
    return {"utc_offset": 3600, "serie": [{"time": (ini + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M"), "gradient": g, "cat": cat} for i in range(48)]}


def test_la_linea_gris_es_la_mejor_hora_para_bandas_bajas():
    # Garòs (42.7N, 0.85E), noviembre: sale el Sol hacia las 06:20 UTC
    assert ventanas.luz(datetime(2026, 11, 23, 6, 20, tzinfo=timezone.utc), 42.7, 0.85) == "gris"
    assert ventanas.luz(datetime(2026, 11, 23, 11, 0, tzinfo=timezone.utc), 42.7, 0.85) == "dia"
    assert ventanas.luz(datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc), 42.7, 0.85) == "noche"
    assert ventanas.hf("gris", 1, 120)["nota"] > ventanas.hf("noche", 1, 120)["nota"]


def test_una_tormenta_geomagnetica_hunde_el_hf():
    assert ventanas.hf("dia", 6, 160)["nota"] < ventanas.hf("dia", 1, 160)["nota"] / 2
    assert "15 m" in ventanas.hf("dia", 1, 160)["bandas"] and "15 m" not in ventanas.hf("dia", 1, 80)["bandas"][:2]


def test_construir_da_16_tramos_con_notas_y_mejores():
    pasos = [{"satelite": "ISS (ZARYA)", "max_el": 70, "aos": "2026-11-23T14:30:00+00:00", "duracion_s": 480}]
    r = ventanas.construir(42.7, 0.85, ahora=_ahora(), horas_campo=_horas_campo(), tropo_serie=_tropo("conducto", -170), solar={"kp": 1, "sfi": 130}, pasos=pasos)
    assert len(r["tramos"]) == 16 and r["tramos"][0]["desde"].startswith("2026-11-23T11:00")
    t = next(x for x in r["tramos"] if x["desde"].startswith("2026-11-23T14:00"))
    assert t["sat"]["nota"] >= 80 and t["sat"]["pasos"][0]["satelite"] == "ISS (ZARYA)"
    assert t["tropo"]["cat"] == "conducto" and t["tropo"]["nota"] >= 90 and t["total"] == max(t["hf"]["nota"], t["tropo"]["nota"], t["sat"]["nota"])
    assert r["mejores"]["sat"] == [t["desde"]] and r["modo"] == "fijo"


def test_en_portatil_el_tiempo_adverso_baja_la_nota_o_la_anula():
    base = dict(ahora=_ahora(), tropo_serie=_tropo(), solar={"kp": 1, "sfi": 130}, pasos=[])
    fijo = ventanas.construir(42.7, 0.85, horas_campo=_horas_campo(2, ["Rachas de 70 km/h: no montes mástil"]), modo="fijo", **base)
    port = ventanas.construir(42.7, 0.85, horas_campo=_horas_campo(2, ["Rachas de 70 km/h: no montes mástil"]), modo="portatil", **base)
    amb = ventanas.construir(42.7, 0.85, horas_campo=_horas_campo(1, ["Lluvia (0.8 mm/h)"]), modo="portatil", **base)
    assert fijo["tramos"][0]["total"] > 0 and all(t["total"] == 0 for t in port["tramos"])
    assert 0 < amb["tramos"][0]["total"] < fijo["tramos"][0]["total"] and port["tramos"][0]["campo"]["motivos"]


def test_sin_datos_de_tropo_ni_solares_no_rompe():
    r = ventanas.construir(42.7, 0.85, ahora=_ahora(), horas_campo=[], tropo_serie=None, solar={}, pasos=[], modo="inventado")
    assert r["modo"] == "fijo" and r["tramos"][0]["tropo"]["cat"] is None and r["tramos"][0]["campo"]["nivel"] == 0
