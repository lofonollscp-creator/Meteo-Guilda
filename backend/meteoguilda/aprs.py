"""Parser de paquetes meteorológicos APRS (APRS101 cap. 12) -> unidades SI."""
from __future__ import annotations

import re
from dataclasses import dataclass

MPH_TO_MS = 0.44704
IN100_TO_MM = 0.254


@dataclass
class WeatherObs:
    callsign: str
    lat: float | None = None
    lon: float | None = None
    wind_dir: int | None = None        # grados
    wind_ms: float | None = None       # viento medio (m/s)
    gust_ms: float | None = None       # racha (m/s)
    temp_c: float | None = None
    rain_1h_mm: float | None = None
    rain_24h_mm: float | None = None
    humidity: int | None = None        # %
    pressure_hpa: float | None = None


_NUM = r"(?:\d{{{n}}}|\.{{{n}}}| {{{n}}})"
_WX = re.compile(
    r"(?:(?P<dir2>\d{3}|\.{3}| {3})/(?P<spd2>\d{3}|\.{3}| {3}))?"
    r"(?:c(?P<c>\d{3}|\.{3}| {3}))?"
    r"(?:s(?P<s>\d{3}|\.{3}| {3}))?"
    r"(?:g(?P<g>\d{3}|\.{3}| {3}))?"
    r"(?:t(?P<t>-\d{2}|\d{3}|\.{3}| {3}))?"
    r"(?:r(?P<r>\d{3}|\.{3}| {3}))?"
    r"(?:p(?P<p>\d{3}|\.{3}| {3}))?"
    r"(?:P(?P<P>\d{3}|\.{3}| {3}))?"
    r"(?:h(?P<h>\d{2}|\.{2}| {2}))?"
    r"(?:b(?P<b>\d{5}|\.{5}| {5}))?"
)
_POS = re.compile(r"(\d{2})(\d{2}\.\d{2})([NS])(.)(\d{3})(\d{2}\.\d{2})([EW])(.)")


def _int(v: str | None) -> int | None:
    if v is None or not re.fullmatch(r"-?\d+", v):
        return None
    return int(v)


def _parse_pos(s: str) -> tuple[float, float, str] | None:
    m = _POS.match(s)
    if not m:
        return None
    lat = int(m[1]) + float(m[2]) / 60
    lon = int(m[5]) + float(m[6]) / 60
    if m[3] == "S":
        lat = -lat
    if m[7] == "W":
        lon = -lon
    return lat, lon, m[8]


def parse_packet(line: str) -> WeatherObs | None:
    """Parsea una línea APRS-IS `ORIGEN>RUTA:payload`. Devuelve None si no es meteo.

    Soporta posición sin comprimir (`!`, `=`, `/`, `@`) con símbolo meteo `_`
    y paquetes meteo sin posición (`_`). Posición comprimida: no soportada aún.
    """
    if ":" not in line or ">" not in line.split(":", 1)[0]:
        return None
    header, body = line.split(":", 1)
    call = header.split(">", 1)[0].strip()
    if not body:
        return None
    dt = body[0]
    lat = lon = None
    if dt in "!=":
        pos, rest = _parse_pos(body[1:]), body[1:]
        offset = 1
    elif dt in "/@":
        pos, rest = _parse_pos(body[8:]), body[8:]
        offset = 8
    elif dt == "_":
        pos, rest, offset = None, body[9:], 9
        wx = rest
        return _build(call, None, None, wx)
    else:
        return None
    if not pos or pos[2] != "_":  # solo símbolo meteo
        return None
    lat, lon, _ = pos
    wx = body[offset + 19:]
    return _build(call, lat, lon, wx)


def _build(call: str, lat, lon, wx: str) -> WeatherObs | None:
    m = _WX.match(wx)
    if not m or not m.group(0):
        return None
    g = m.groupdict()
    d = _int(g["dir2"]) if g["dir2"] else _int(g["c"])
    sp = _int(g["spd2"]) if g["spd2"] else _int(g["s"])
    gust = _int(g["g"])
    t = _int(g["t"])
    r = _int(g["r"])
    p = _int(g["p"])
    h = _int(g["h"])
    b = _int(g["b"])
    obs = WeatherObs(
        callsign=call, lat=lat, lon=lon,
        wind_dir=d,
        wind_ms=round(sp * MPH_TO_MS, 1) if sp is not None else None,
        gust_ms=round(gust * MPH_TO_MS, 1) if gust is not None else None,
        temp_c=round((t - 32) * 5 / 9, 1) if t is not None else None,
        rain_1h_mm=round(r * IN100_TO_MM, 1) if r is not None else None,
        rain_24h_mm=round(p * IN100_TO_MM, 1) if p is not None else None,
        humidity=(100 if h == 0 else h) if h is not None else None,
        pressure_hpa=b / 10 if b is not None else None,
    )
    fields = (obs.wind_ms, obs.gust_ms, obs.temp_c, obs.humidity, obs.pressure_hpa)
    return obs if any(f is not None for f in fields) else None
