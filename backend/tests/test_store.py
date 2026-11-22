from meteoguilda import store
from meteoguilda.aprs import parse_packet


def pk(call, lat, lon, t="050"):
    la = f"{int(lat):02d}{(lat - int(lat)) * 60:05.2f}N"
    lo = f"{int(abs(lon)):03d}{(abs(lon) - int(abs(lon))) * 60:05.2f}{'W' if lon < 0 else 'E'}"
    return parse_packet(f"{call}>APRS:@092345z{la}/{lo}_220/004g005t{t}h50b10132")


def test_insert_latest_nearby_line(clean):
    store.insert_many([pk("A", 42.70, 0.86), pk("B", 42.75, 0.90), pk("C", 41.49, 2.36), pk("A", 42.70, 0.86, "060")])
    last = {o.callsign: o for o in store.latest(3600)}
    assert set(last) == {"A", "B", "C"} and last["A"].temp_c == 15.6      # la más reciente de A
    near = store.nearby(42.71, 0.86, 20)
    assert [o.callsign for _, o in near] == ["A", "B"] and near[0][0] < 3
    box = store.latest(3600, bbox=(41.0, 2.0, 42.0, 3.0))
    assert [o.callsign for o in box] == ["C"]
    along = store.near_line([(42.60, 0.80), (42.80, 0.95)], radius_km=6)
    assert {o.callsign for _, o in along} == {"A", "B"}
