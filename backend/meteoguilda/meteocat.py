"""Predicción oficial del Servei Meteorològic de Catalunya (API del Meteocat, pronóstico municipal a 8 días).

La clave (`METEOCAT_API_KEY`, gratuita) tiene un plan de PREDICCIÓN de solo 100 consultas al mes. Por eso:
- Cada municipio se guarda 18 h (el Meteocat actualiza la predicción una vez al día) en memoria y en `kv_cache`, así que un reinicio no gasta cuota.
- Hay un tope propio de 90 consultas al mes y 5 al día (contadores en `kv_cache`): si se agota, se devuelve lo último guardado marcado como antiguo.
- Solo se consulta para puntos a menos de 15 km del centro de un municipio catalán.
Nunca lanza: ante cualquier fallo devuelve {"disponible": False, ...}; la previsión de los modelos no depende de esto."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from importlib import resources

import httpx

log = logging.getLogger("meteoguilda.meteocat")

API = "https://api.meteo.cat/pronostic/v1/municipal"
FRESCO_S = 18 * 3600          # la predicción se renueva una vez al día
MAX_MES, MAX_DIA = 90, 5      # de las 100 consultas/mes del plan; el resto queda de margen
RADIO_KM = 15
CIELO = {1: "Cielo despejado", 2: "Nubes altas", 3: "Poco o medio nuboso", 4: "Muy nuboso", 5: "Nubes medias con llovizna", 6: "Lluvia",
         7: "Chubasco", 8: "Tormenta", 9: "Tormenta con granizo", 10: "Nevada", 11: "Niebla", 12: "Neblina", 13: "Chubasco de nieve",
         20: "Entre medio y muy nuboso", 21: "Nuboso", 22: "Calima", 23: "Medio nuboso con chubascos", 24: "Chubasco con tormenta",
         25: "Chubasco con tormenta y granizo", 26: "Chubasco de nieve", 27: "Nieve débil", 28: "Chubasco de nieve", 29: "Chubasco de aguanieve",
         30: "Aguanieve", 31: "Muy nuboso con chubascos", 32: "Nuboso con llovizna"}

_mem: dict[str, tuple[float, dict]] = {}
_municipios: list[list] | None = None


def _lista() -> list[list]:
    global _municipios
    if _municipios is None:
        _municipios = json.loads(resources.files("meteoguilda").joinpath("data/municipis_cat.json").read_text())
    return _municipios


def _km(la1: float, lo1: float, la2: float, lo2: float) -> float:
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def municipio_cercano(lat: float, lon: float) -> dict | None:
    if not (40.4 <= lat <= 43.0 and 0.0 <= lon <= 3.5):
        return None
    km, m = min(((_km(lat, lon, m[2], m[3]), m) for m in _lista()), key=lambda x: x[0])
    return {"codi": m[0], "nom": m[1], "dist_km": round(km, 1)} if km <= RADIO_KM else None


# ---- persistencia (kv_cache) ----
def _db_get(k: str) -> str | None:
    try:
        from .db import pool
        with pool().connection() as con:
            r = con.execute("SELECT v FROM kv_cache WHERE k = %s", [k]).fetchone()
        return r["v"] if r else None
    except Exception:  # noqa: BLE001
        return None


def _db_put(k: str, v: str) -> None:
    try:
        from .db import pool
        with pool().connection() as con:
            con.execute("INSERT INTO kv_cache (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = now()", [k, v])
    except Exception:  # noqa: BLE001
        pass


def _consumo(ahora: datetime) -> tuple[int, int]:
    return int(_db_get(f"meteocat:mes:{ahora:%Y-%m}") or 0), int(_db_get(f"meteocat:dia:{ahora:%Y-%m-%d}") or 0)


def _anota(ahora: datetime) -> None:
    mes, dia = _consumo(ahora)
    _db_put(f"meteocat:mes:{ahora:%Y-%m}", str(mes + 1))
    _db_put(f"meteocat:dia:{ahora:%Y-%m-%d}", str(dia + 1))


def _parsea(raw: dict) -> list[dict]:
    dias = []
    for d in raw.get("dies", []):
        v = d.get("variables", {})
        num = lambda k: float(v[k]["valor"]) if isinstance(v.get(k), dict) and v[k].get("valor") not in (None, "") else None  # noqa: E731
        cod = v.get("estatCel", {}).get("valor")
        dias.append({"fecha": str(d.get("data", ""))[:10], "tmax": num("tmax"), "tmin": num("tmin"), "lluvia_prob_pct": num("precipitacio"),
                     "cielo": CIELO.get(int(cod)) if cod is not None and str(cod).isdigit() else None,
                     "cielo_codigo": int(cod) if cod is not None and str(cod).isdigit() else None})
    return dias


def _salida(m: dict, dias: list[dict], ts: float, antiguo: bool) -> dict:
    return {"disponible": True, "fuente": "Meteocat (predicción oficial)", "municipio": m["nom"], "codigo": m["codi"], "dist_km": m["dist_km"],
            "dias": dias, "actualizado": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "antiguo": antiguo,
            "enlace": "https://www.meteo.cat/prediccio/municipal/" + m["codi"]}


async def prediccion(http: httpx.AsyncClient, lat: float, lon: float, ahora: datetime | None = None) -> dict:
    key = os.environ.get("METEOCAT_API_KEY")
    m = municipio_cercano(lat, lon)
    if not key:
        return {"disponible": False, "motivo": "sin_clave"}
    if not m:
        return {"disponible": False, "motivo": "fuera_de_cataluna"}
    ahora = ahora or datetime.now(timezone.utc)
    k = "meteocat:pred:" + m["codi"]
    hit = _mem.get(k)
    if not hit:
        raw = _db_get(k)
        if raw:
            try:
                j = json.loads(raw)
                hit = _mem[k] = (j["ts"], j)
            except (ValueError, KeyError):
                hit = None
    if hit and time.time() - hit[0] < FRESCO_S:
        return _salida(m, hit[1]["dias"], hit[0], False)
    mes, dia = _consumo(ahora)
    if mes >= MAX_MES or dia >= MAX_DIA:                 # sin cuota propia: lo último que haya, avisando de que es antiguo
        return _salida(m, hit[1]["dias"], hit[0], True) if hit else {"disponible": False, "motivo": "cuota"}
    try:
        _anota(ahora)                                    # se cuenta antes: aunque falle, la consulta pudo gastarse
        r = await http.get(f"{API}/{m['codi']}", headers={"x-api-key": key}, timeout=12)
        r.raise_for_status()
        dias = _parsea(r.json())
        if not dias:
            raise ValueError("respuesta sin días")
    except Exception as e:  # noqa: BLE001
        log.warning("Meteocat: %s", e)
        return _salida(m, hit[1]["dias"], hit[0], True) if hit else {"disponible": False, "motivo": "error"}
    ts = time.time()
    _mem[k] = (ts, {"ts": ts, "dias": dias})
    _db_put(k, json.dumps({"ts": ts, "dias": dias}))
    return _salida(m, dias, ts, False)
