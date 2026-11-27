"""Verificación de los modelos: ¿qué modelo ha acertado más la temperatura en cada lugar principal?

1) `registrar`: al bajar los datos de un lugar principal (4 veces al día: 00, 06, 12 y 18 UTC) se guarda la temperatura que cada modelo prevé a 12 y a 24 h.
2) `verificar`: cuando llega esa hora, se compara con la mediana de las estaciones oficiales cercanas (XEMA y METAR, ≤ 25 km, lectura de ±35 min).
3) `precision`: error absoluto medio (MAE) y sesgo de cada modelo en los últimos días. No se corrigen las cifras de los modelos: solo se enseña quién acierta.
Nada de esto lanza excepciones: si la base de datos o las estaciones fallan, la previsión sigue igual."""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

import httpx

from . import openmeteo, sources
from .db import pool
from .places import PLACES

log = logging.getLogger("meteoguilda.verif")

LEADS = (12, 24)
HORAS_REGISTRO = (0, 6, 12, 18)       # UTC
RADIO_KM = 25
MIN_N = 8                              # comparaciones mínimas para publicar la precisión de un modelo
CONSERVAR_DIAS = 120


def lugar_de(lat: float, lon: float) -> str | None:
    for p in PLACES:
        if abs(p.lat - lat) < 1e-4 and abs(p.lon - lon) < 1e-4:
            return p.slug
    return None


def registrar(raw: dict, lugar: str, ahora: datetime | None = None) -> int:
    """Guarda lo que prevé cada modelo a 12 y 24 h. Devuelve las filas intentadas (0 si no toca registrar a esta hora)."""
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.hour not in HORAS_REGISTRO:
        return 0
    try:
        h = raw["hourly"]; off = timedelta(seconds=raw.get("utc_offset_seconds", 0))
        i0 = openmeteo._start_index(raw, ahora); filas = []
        for lead in LEADS:
            i = i0 + lead
            if i >= len(h["time"]):
                continue
            valida = openmeteo.parse_time(h["time"][i]) - off
            for modelo, serie in openmeteo._named_cols(h, "temperature_2m"):
                if i < len(serie) and serie[i] is not None:
                    filas.append((lugar, modelo, lead, valida.replace(tzinfo=timezone.utc), float(serie[i])))
        with pool().connection() as con:
            for f in filas:
                con.execute("INSERT INTO verif_pred (lugar, modelo, lead_h, valida, pred_c) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", f)
        return len(filas)
    except Exception as e:  # noqa: BLE001
        log.warning("verif.registrar %s: %s", lugar, e)
        return 0


def _pendientes(ahora: datetime) -> list[dict]:
    with pool().connection() as con:
        return con.execute("SELECT DISTINCT lugar, valida FROM verif_pred WHERE obs_c IS NULL AND valida <= %s AND valida > %s",
                           [ahora - timedelta(minutes=5), ahora - timedelta(minutes=100)]).fetchall()


async def verificar(http: httpx.AsyncClient, ahora: datetime | None = None) -> int:
    """Completa con lo medido las predicciones cuya hora acaba de pasar. Devuelve las comparaciones hechas."""
    ahora = ahora or datetime.now(timezone.utc); hechas = 0
    try:
        pend = _pendientes(ahora)
        por_lugar: dict[str, list[datetime]] = {}
        for r in pend:
            por_lugar.setdefault(r["lugar"], []).append(r["valida"])
        for slug, validas in por_lugar.items():
            p = next((x for x in PLACES if x.slug == slug), None)
            if not p:
                continue
            est = [o for o in await sources.nearby(http, p.lat, p.lon, RADIO_KM) if o.get("hora") and o.get("temp_c") is not None]
            for valida in validas:
                xs = [o["temp_c"] for o in est if abs((datetime.fromisoformat(o["hora"].replace("Z", "+00:00")) - valida).total_seconds()) <= 35 * 60]
                if not xs:
                    continue
                with pool().connection() as con:
                    cur = con.execute("UPDATE verif_pred SET obs_c = %s, n_est = %s, verificada = %s WHERE lugar = %s AND valida = %s AND obs_c IS NULL",
                                      [statistics.median(xs), len(xs), ahora, slug, valida])
                    hechas += cur.rowcount
        with pool().connection() as con:
            con.execute("DELETE FROM verif_pred WHERE valida < %s", [ahora - timedelta(days=CONSERVAR_DIAS)])
    except Exception as e:  # noqa: BLE001
        log.warning("verif.verificar: %s", e)
    return hechas


def precision(lugar: str, dias: int = 30, lead_h: int = 24, min_n: int = MIN_N) -> list[dict]:
    """MAE (°C) y sesgo (previsto − medido) de cada modelo en el lugar, de mejor a peor; solo modelos con MIN_N comparaciones."""
    try:
        with pool().connection() as con:
            rows = con.execute("SELECT modelo, count(*) AS n, avg(abs(pred_c - obs_c)) AS mae, avg(pred_c - obs_c) AS sesgo FROM verif_pred "
                               "WHERE lugar = %s AND lead_h = %s AND obs_c IS NOT NULL AND valida > now() - make_interval(days => %s) "
                               "GROUP BY modelo HAVING count(*) >= %s ORDER BY avg(abs(pred_c - obs_c))", [lugar, lead_h, dias, min_n]).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning("verif.precision: %s", e)
        return []
    return [{"modelo": r["modelo"], "etiqueta": openmeteo.MODEL_LABELS.get(r["modelo"], r["modelo"]), "n": r["n"], "mae_c": round(r["mae"], 2),
             "sesgo_c": round(r["sesgo"], 2)} for r in rows]


MIN_N_PESOS = 24                       # comparaciones por modelo antes de dejar que el historial mueva la media (≈ 6 días a 4 registros/día)


def pesos(lugar: str) -> dict[str, float] | None:
    """Pesos por modelo para la media de temperatura de un lugar principal, o None (media simple).
    Solo con MG_PONDERAR=1 y al menos 3 modelos con MIN_N_PESOS comparaciones a 24 h. Peso = (MAE medio / MAE del modelo)², acotado a 0,5–2,
    para que ningún modelo mande ni desaparezca; el sesgo no se corrige."""
    import os
    if os.environ.get("MG_PONDERAR") != "1":
        return None
    p = precision(lugar, 30, 24, MIN_N_PESOS)
    if len(p) < 3:
        return None
    media = statistics.fmean(max(x["mae_c"], 0.1) for x in p)
    return {x["modelo"]: round(min(2.0, max(0.5, (media / max(x["mae_c"], 0.1)) ** 2)), 3) for x in p}
