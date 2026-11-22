from meteoguilda.aprs import parse_packet
from meteoguilda.qc import quality_flags, range_ok


def test_position_weather():
    o = parse_packet("EA1ABC-13>APRS,TCPIP*:@092345z4303.50N/00541.75W_220/004g005t050r000p000P000h50b10132")
    assert o.callsign == "EA1ABC-13"
    assert round(o.lat, 3) == 43.058 and round(o.lon, 3) == -5.696
    assert o.wind_dir == 220
    assert o.wind_ms == 1.8 and o.gust_ms == 2.2
    assert o.temp_c == 10.0
    assert o.humidity == 50 and o.pressure_hpa == 1013.2


def test_positionless():
    o = parse_packet("CW1234>APRS,TCPIP*:_10090556c220s004g005t077r001p002P003h00b09900")
    assert o.wind_dir == 220 and o.temp_c == 25.0
    assert o.humidity == 100 and o.pressure_hpa == 990.0
    assert o.rain_1h_mm == 0.3 and o.lat is None


def test_missing_values_and_non_weather():
    o = parse_packet("X>APRS:!4303.50N/00541.75W_.../...g...t050")
    assert o.temp_c == 10.0 and o.wind_ms is None
    assert parse_packet("X>APRS:!4303.50N/00541.75W>comentario") is None
    assert parse_packet("X>APRS:>status") is None
    assert parse_packet("basura") is None


def test_qc_range_and_outlier():
    base = "@092345z4303.50N/00541.75W_220/004g005t050h50b10132"
    ok = [parse_packet(f"A{i}>APRS:{base}") for i in range(4)]
    bad = parse_packet("B>APRS:@092345z4303.50N/00541.75W_220/004g005t120h50b10132")  # 48.9 °C ok
    hot = parse_packet("C>APRS:@092345z4303.50N/00541.75W_220/004g005t100h50b10132")  # 37.8 °C
    assert range_ok(bad)
    flags = quality_flags(ok + [hot])
    assert flags["C"] == "outlier" and flags["A0"] == "ok"
    impossible = parse_packet("D>APRS:@092345z4303.50N/00541.75W_220/004g005t-90h50b10132")
    assert impossible is not None and not range_ok(impossible)
