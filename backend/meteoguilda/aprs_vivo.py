"""APRS en vivo y clúster DX.

APRS: el ingestor (ingest.py) ya recibe TODOS los paquetes de los círculos de 60 km de los lugares principales; además de la meteo, ahora guarda la
ÚLTIMA posición de cada estación (posición sin comprimir, comprimida y Mic-E), su símbolo, velocidad, rumbo y un comentario corto. Solo la última
(no hay recorridos) y se borra a las 24 h. Es información que las estaciones publican abiertamente en APRS-IS (como aprs.fi).

Clúster DX: spots públicos de HamQTH (CSV) y, si falla, de DX Summit (JSON). Una sola descarga compartida cada 2 min como mucho, con la última lista
buena guardada 30 min si la fuente cae: el uso de las personas nunca multiplica las consultas a la fuente."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query

from .aprs import _parse_pos
from .db import pool
from .store import clamp_bbox

log = logging.getLogger("meteoguilda.aprs_vivo")
router = APIRouter()
GUARDAR_H = 24
NUDO_KMH = 1.852


# ---------- APRS: posiciones ----------
def _b91(s: str) -> int:
    n = 0
    for c in s:
        n = n * 91 + (ord(c) - 33)
    return n


def _comprimida(s: str) -> tuple[float, float, str, str, str] | None:
    """Posición comprimida (APRS101 cap. 9): /YYYYXXXX$cs T → (lat, lon, tabla, símbolo, resto)."""
    if len(s) < 13 or not all(33 <= ord(c) <= 124 for c in s[1:9]):
        return None
    lat = 90 - _b91(s[1:5]) / 380926
    lon = -180 + _b91(s[5:9]) / 190463
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon, s[0], s[9], s[13:]


def _mic_e(dest: str, info: str) -> dict | None:
    """Mic-E (APRS101 cap. 10): latitud en el destino y longitud, velocidad y rumbo en el cuerpo."""
    d = dest.split("-")[0]
    if len(d) < 6 or len(info) < 9:
        return None
    dig = []
    for c in d[:6]:
        if c.isdigit():
            dig.append(int(c))
        elif "A" <= c <= "J":
            dig.append(ord(c) - 65)
        elif "P" <= c <= "Y":
            dig.append(ord(c) - 80)
        elif c in "KLZ":
            dig.append(0)
        else:
            return None
    lat = dig[0] * 10 + dig[1] + (dig[2] * 10 + dig[3] + (dig[4] * 10 + dig[5]) / 100) / 60
    norte, mas100, oeste = "P" <= d[3] <= "Z", "P" <= d[4] <= "Z", "P" <= d[5] <= "Z"
    b = [ord(c) - 28 for c in info[1:7]]
    lon_g = b[0] + (100 if mas100 else 0)
    if 180 <= lon_g <= 189:
        lon_g -= 80
    elif 190 <= lon_g <= 199:
        lon_g -= 190
    lon_m = b[1] - 60 if b[1] >= 60 else b[1]
    lon = lon_g + (lon_m + b[2] / 100) / 60
    vel = b[3] * 10 + b[4] // 10
    vel = vel - 800 if vel >= 800 else vel
    rumbo = (b[4] % 10) * 100 + b[5]
    rumbo = rumbo - 400 if rumbo >= 400 else rumbo
    lat, lon = (lat if norte else -lat), (-lon if oeste else lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"lat": lat, "lon": lon, "simbolo": info[8] + info[7], "velocidad_kmh": round(vel * NUDO_KMH) if vel else 0, "rumbo": rumbo or None,
            "comentario": info[9:]}


_CURSO = re.compile(r"^(\d{3})/(\d{3})")
_ALT = re.compile(r"/A=(-?\d{6})")


def parse_posicion(line: str) -> dict | None:
    """Última posición de una estación a partir de una línea APRS-IS: {callsign, lat, lon, simbolo, velocidad_kmh, rumbo, altitud_m, comentario}."""
    if ":" not in line or ">" not in line.split(":", 1)[0]:
        return None
    header, body = line.split(":", 1)
    call, ruta = header.split(">", 1)
    call = call.strip().upper()
    if not body or not re.fullmatch(r"[A-Z0-9\-]{3,12}", call):
        return None
    dt = body[0]
    out = None
    if dt in "`'":
        out = _mic_e(ruta.split(",")[0], body)
    elif dt in "!=/@":
        s = body[1:] if dt in "!=" else body[8:]
        if s[:1].isdigit():
            p = _parse_pos(s)
            m = re.match(r"\d{4}\.\d{2}[NS](.)", s)
            if p and m:
                resto = s[19:]
                out = {"lat": p[0], "lon": p[1], "simbolo": m.group(1) + p[2], "comentario": resto}
                if (c := _CURSO.match(resto)):
                    out.update(rumbo=int(c.group(1)) or None, velocidad_kmh=round(int(c.group(2)) * NUDO_KMH), comentario=resto[7:])
        else:
            c = _comprimida(s)
            if c:
                out = {"lat": c[0], "lon": c[1], "simbolo": c[2] + c[3], "comentario": c[4]}
    if not out:
        return None
    alt = _ALT.search(out.get("comentario") or "")
    com = _ALT.sub("", out.get("comentario") or "")
    com = "".join(ch for ch in com if ch.isprintable()).strip()[:60] or None
    return {"callsign": call, "lat": round(out["lat"], 5), "lon": round(out["lon"], 5), "simbolo": out["simbolo"][:2],
            "velocidad_kmh": out.get("velocidad_kmh"), "rumbo": out.get("rumbo"), "altitud_m": round(int(alt.group(1)) * 0.3048) if alt else None,
            "comentario": com}


def guardar(items: list[dict]) -> int:
    """Inserta o actualiza la última posición de cada estación."""
    if not items:
        return 0
    ultimo = {i["callsign"]: i for i in items}
    with pool().connection() as con:
        con.cursor().executemany(
            "INSERT INTO aprs_estaciones (callsign, ts, geom, simbolo, velocidad_kmh, rumbo, altitud_m, comentario) VALUES "
            "(%s, clock_timestamp(), ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s, %s, %s, %s) ON CONFLICT (callsign) DO UPDATE SET "
            "ts = EXCLUDED.ts, geom = EXCLUDED.geom, simbolo = EXCLUDED.simbolo, velocidad_kmh = EXCLUDED.velocidad_kmh, rumbo = EXCLUDED.rumbo, "
            "altitud_m = EXCLUDED.altitud_m, comentario = EXCLUDED.comentario",
            [(i["callsign"], i["lon"], i["lat"], i["simbolo"], i["velocidad_kmh"], i["rumbo"], i["altitud_m"], i["comentario"]) for i in ultimo.values()])
    return len(ultimo)


def purgar() -> None:
    with pool().connection() as con:
        con.execute("DELETE FROM aprs_estaciones WHERE ts < now() - make_interval(hours => %s)", [GUARDAR_H])


TIPOS = {"_": "meteo", ">": "coche", "k": "camión", "v": "furgoneta", "[": "persona", "b": "bici", "<": "moto", "s": "barco", "Y": "velero",
         "#": "digipeater", "&": "igate", "-": "casa", "r": "repetidor", "'": "avioneta", "^": "avión", "O": "globo", "a": "ambulancia", "f": "bomberos"}


def tipo(simbolo: str | None) -> str:
    if not simbolo or len(simbolo) < 2:
        return "estación"
    if simbolo[1] in "#&" and simbolo[0] != "/":
        return "digipeater" if simbolo[1] == "#" else "igate"
    return TIPOS.get(simbolo[1], "estación")


def recientes(bbox=None, max_age_min: int = 60, limite: int = 1000, cerca: tuple[float, float, float] | None = None) -> list[dict]:
    where, args = ["ts > now() - make_interval(mins => %s)"], [max_age_min]
    bb = clamp_bbox(bbox)
    if bb:
        where.append("geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)::geography")
        args += [bb[1], bb[0], bb[3], bb[2]]
    orden, dist = "ts DESC", "NULL"
    if cerca:
        where.append("ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)")
        args += [cerca[1], cerca[0], cerca[2] * 1000]
        dist = f"ST_Distance(geom, ST_SetSRID(ST_MakePoint({float(cerca[1])}, {float(cerca[0])}), 4326)::geography) / 1000"
        orden = "dist"
    with pool().connection() as con:
        filas = con.execute(f"SELECT callsign, ts, ST_Y(geom::geometry) AS lat, ST_X(geom::geometry) AS lon, simbolo, velocidad_kmh, rumbo, altitud_m, comentario, "
                            f"{dist} AS dist FROM aprs_estaciones WHERE {' AND '.join(where)} ORDER BY {orden} LIMIT %s", args + [limite]).fetchall()
    ahora = datetime.now(timezone.utc)
    return [{"indicativo": f["callsign"], "lat": round(f["lat"], 5), "lon": round(f["lon"], 5), "tipo": tipo(f["simbolo"]), "simbolo": f["simbolo"],
             "hace_min": int((ahora - f["ts"]).total_seconds() // 60), "velocidad_kmh": f["velocidad_kmh"], "rumbo": f["rumbo"], "altitud_m": f["altitud_m"],
             "comentario": f["comentario"], **({"dist_km": round(f["dist"], 1)} if f["dist"] is not None else {})} for f in filas]


@router.get("/api/aprs")
def aprs(bbox: str | None = Query(None, max_length=100), lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
         km: float = Query(50, gt=0, le=300), max_age_min: int = Query(60, ge=1, le=1440), limite: int = Query(500, ge=1, le=2000)):
    """Estaciones APRS recientes (última posición de cada una) en un recuadro (`bbox` = sur,oeste,norte,este) o cerca de un punto (lat, lon, km).
    Cubre los alrededores (60 km) de los lugares principales, que es lo que escucha el ingestor."""
    caja = None
    if bbox:
        try:
            caja = tuple(map(float, bbox.split(",")))
            assert len(caja) == 4
        except (ValueError, AssertionError) as e:
            raise HTTPException(422, "bbox: sur,oeste,norte,este") from e
    cerca = (lat, lon, km) if lat is not None and lon is not None else None
    try:
        est = recientes(caja, max_age_min, limite, cerca)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, "Base de datos no disponible") from e
    return {"estaciones": est, "total": len(est), "max_age_min": max_age_min,
            "nota": "Última posición de cada estación en APRS-IS (red pública); se guarda 24 h. Zona: 60 km alrededor de los lugares principales."}


# ---------- clúster DX ----------
HAMQTH = "https://www.hamqth.com/dxc_csv.php"
DXSUMMIT = "http://www.dxsummit.fi/api/v1/spots"
TTL_S, INTERVALO_S, GUARDAR_S = 120, 60, 1800
_dx: dict = {"t": 0.0, "intento": 0.0, "spots": [], "fuente": None}
_lock: asyncio.Lock | None = None
BANDAS = [(1800, 2000, "160m"), (3500, 4000, "80m"), (5300, 5450, "60m"), (7000, 7300, "40m"), (10100, 10150, "30m"), (14000, 14350, "20m"),
          (18068, 18168, "17m"), (21000, 21450, "15m"), (24890, 24990, "12m"), (28000, 29700, "10m"), (50000, 54000, "6m"), (70000, 70500, "4m"),
          (144000, 148000, "2m"), (430000, 440000, "70cm")]
MODOS = ("FT8", "FT4", "CW", "SSB", "RTTY", "PSK", "JS8", "FM", "SSTV", "USB", "LSB")


def banda(khz: float | None) -> str | None:
    return next((b for lo, hi, b in BANDAS if khz is not None and lo <= khz <= hi), None)


def modo(comentario: str, khz: float | None) -> str | None:
    up = (comentario or "").upper()
    m = next((x for x in MODOS if re.search(rf"\b{x}\b", up)), None)
    if m in ("USB", "LSB"):
        return "SSB"
    if m:
        return m
    if khz is not None:
        frac = khz % 1000
        if banda(khz) in ("160m", "80m", "40m", "30m", "20m", "17m", "15m", "12m", "10m") and frac < 70:
            return "CW"
    return None


def leer_hamqth(texto: str) -> list[dict]:
    out = []
    for linea in texto.splitlines():
        f = linea.split("^")
        if len(f) < 10:
            continue
        try:
            khz = float(f[1])
            hora = datetime.strptime(f[4].strip(), "%H%M %Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
        out.append({"dx": f[2].strip().upper(), "spotter": f[0].strip().upper(), "frecuencia_khz": khz, "banda": (f[8].strip().lower() or banda(khz)),
                    "modo": modo(f[3], khz), "comentario": f[3].strip()[:80], "hora": hora, "pais": f[9].strip() or None, "continente": f[7].strip() or None,
                    "lotw": f[5].strip() == "L"})
    return out


def leer_dxsummit(datos: list) -> list[dict]:
    out = []
    for d in datos if isinstance(datos, list) else []:
        try:
            khz = float(d["frequency"])
            hora = datetime.fromisoformat(d["time"]).replace(tzinfo=timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"dx": str(d.get("dx_call", "")).upper(), "spotter": str(d.get("de_call", "")).split("-@")[0].upper(), "frecuencia_khz": khz, "banda": banda(khz),
                    "modo": modo(d.get("info", ""), khz), "comentario": str(d.get("info", ""))[:80], "hora": hora, "pais": d.get("dx_country"), "continente": None, "lotw": None})
    return out


async def spots(http: httpx.AsyncClient) -> dict:
    """Lista compartida de spots (caché de 2 min; como mucho un intento por minuto; si la fuente cae, la última buena durante 30 min)."""
    global _lock
    ahora = time.time()
    if ahora - _dx["t"] < TTL_S or ahora - _dx["intento"] < INTERVALO_S:
        return _dx
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if time.time() - _dx["t"] < TTL_S or time.time() - _dx["intento"] < INTERVALO_S:
            return _dx
        _dx["intento"] = time.time()
        for nombre, f in (("HamQTH", _de_hamqth), ("DX Summit", _de_dxsummit)):
            try:
                lista = await f(http)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("clúster DX %s: %s", nombre, e)
                continue
            if lista:
                _dx.update(t=time.time(), spots=lista, fuente=nombre)
                break
        if time.time() - _dx["t"] > GUARDAR_S:
            _dx.update(spots=[], fuente=None)
    return _dx


async def _de_hamqth(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(HAMQTH, params={"limit": 200}, timeout=20)
    r.raise_for_status()
    return leer_hamqth(r.text)


async def _de_dxsummit(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(DXSUMMIT, params={"limit": 200}, timeout=20)
    r.raise_for_status()
    return leer_dxsummit(r.json())


def filtrar(lista: list[dict], banda_: str | None, modo_: str | None, continente: str | None, buscar: str | None, limite: int) -> list[dict]:
    out = [s for s in lista if (not banda_ or s["banda"] == banda_.lower()) and (not modo_ or (s["modo"] or "") == modo_.upper())
           and (not continente or (s["continente"] or "") == continente.upper()) and (not buscar or buscar.upper() in s["dx"] or buscar.upper() in (s["pais"] or "").upper())]
    return out[:limite]
