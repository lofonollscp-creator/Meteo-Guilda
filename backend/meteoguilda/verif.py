"""Verificación de los modelos: ¿qué modelo ha acertado más la temperatura en cada lugar principal?

1) `registrar`: al bajar los datos de un lugar principal (4 veces al día: 00, 06, 12 y 18 UTC) se guarda la temperatura que cada modelo prevé a 12 y a 24 h.
2) `verificar`: cuando llega esa hora, se compara con la mediana de las estaciones oficiales cercanas (XEMA y METAR, ≤ 25 km, lectura de ±35 min).
3) `precision`: error absoluto medio (MAE) y sesgo de cada modelo en los últimos días. No se corrigen las cifras de los modelos: solo se enseña quién acierta.
4) Lugares extra (R4, nov-2026): cada persona puede pedir que se verifiquen hasta 5 lugares suyos (favoritos o Mi ubicación), con un tope global de
   MAX_EXTRA_TOTAL. Se registran a las mismas horas con UNA consulta por lugar cada 6 h, y solo si Open-Meteo va holgado (cuota del día < 60 % y sin freno).
5) `informe`: informe mensual por lugar (MAE, sesgo y comparaciones de cada modelo a 12 y 24 h, el que más acierta y cómo iba el mes anterior).
Nada de esto lanza excepciones: si la base de datos o las estaciones fallan, la previsión sigue igual."""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
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


MAX_EXTRA_PERSONA = 5
MAX_EXTRA_TOTAL = 40
CUOTA_MAX_PCT = 60                     # no se gastan consultas en lugares extra si el día ya va por encima de este % del límite de Open-Meteo
_extras: tuple[float, dict[str, dict]] = (0.0, {})


def clave_extra(lat: float, lon: float) -> str:
    return f"x:{round(lat, 2):.2f},{round(lon, 2):.2f}"


def extras(refrescar: bool = False) -> dict[str, dict]:
    """Lugares extra que alguien quiere verificar: clave → {lat, lon, nombre} (caché de 5 min)."""
    global _extras
    if not refrescar and time.time() - _extras[0] < 300:
        return _extras[1]
    try:
        with pool().connection() as con:
            filas = con.execute("SELECT e.clave, e.lat, e.lon, e.nombre FROM verif_extra e WHERE EXISTS (SELECT 1 FROM verif_extra_sub s WHERE s.clave = e.clave) "
                                "ORDER BY e.creado LIMIT %s", [MAX_EXTRA_TOTAL]).fetchall()
        _extras = (time.time(), {f["clave"]: {"lat": f["lat"], "lon": f["lon"], "nombre": f["nombre"]} for f in filas})
    except Exception as e:  # noqa: BLE001
        log.warning("verif.extras: %s", e)
    return _extras[1]


def lugar_de(lat: float, lon: float) -> str | None:
    for p in PLACES:
        if abs(p.lat - lat) < 1e-4 and abs(p.lon - lon) < 1e-4:
            return p.slug
    k = clave_extra(lat, lon)
    return k if k in extras() and abs(round(lat, 2) - lat) < 1e-4 and abs(round(lon, 2) - lon) < 1e-4 else None


def coords(lugar: str) -> tuple[float, float, str] | None:
    p = next((x for x in PLACES if x.slug == lugar), None)
    if p:
        return p.lat, p.lon, p.name
    e = extras().get(lugar)
    return (e["lat"], e["lon"], e["nombre"]) if e else None


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
            c = coords(slug)
            if not c:
                continue
            est = [o for o in await sources.nearby(http, c[0], c[1], RADIO_KM) if o.get("hora") and o.get("temp_c") is not None]
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


# ---------- lugares extra de cada persona ----------
def mis_lugares(sub: str) -> list[dict]:
    with pool().connection() as con:
        filas = con.execute("SELECT e.clave, e.lat, e.lon, e.nombre, (SELECT count(*) FROM verif_pred v WHERE v.lugar = e.clave AND v.obs_c IS NOT NULL) AS comparaciones "
                            "FROM verif_extra_sub s JOIN verif_extra e USING (clave) WHERE s.sub = %s ORDER BY s.creado", [sub]).fetchall()
    return filas


def poner_mis_lugares(sub: str, lugares: list[dict]) -> list[dict]:
    """Sustituye la lista de lugares extra de la persona (máx. MAX_EXTRA_PERSONA). ValueError si se pasa del tope global."""
    nuevos = {}
    for l in lugares[:MAX_EXTRA_PERSONA]:
        la, lo = round(float(l["lat"]), 2), round(float(l["lon"]), 2)
        nuevos[clave_extra(la, lo)] = (la, lo, (l.get("nombre") or "").strip()[:80] or f"{la:.2f}, {lo:.2f}")
    with pool().connection() as con:
        with con.transaction():
            existentes = {r["clave"] for r in con.execute("SELECT clave FROM verif_extra e WHERE EXISTS (SELECT 1 FROM verif_extra_sub s WHERE s.clave = e.clave AND s.sub <> %s)", [sub]).fetchall()}
            total = len(existentes | set(nuevos))
            if total > MAX_EXTRA_TOTAL:
                raise ValueError(f"Ya se verifican {MAX_EXTRA_TOTAL} lugares extra en este servidor (es el tope para no gastar la cuota de Open-Meteo)")
            con.execute("DELETE FROM verif_extra_sub WHERE sub = %s", [sub])
            for k, (la, lo, nom) in nuevos.items():
                con.execute("INSERT INTO verif_extra (clave, lat, lon, nombre) VALUES (%s,%s,%s,%s) ON CONFLICT (clave) DO NOTHING", [k, la, lo, nom])
                con.execute("INSERT INTO verif_extra_sub (sub, clave) VALUES (%s,%s) ON CONFLICT DO NOTHING", [sub, k])
            con.execute("DELETE FROM verif_extra e WHERE NOT EXISTS (SELECT 1 FROM verif_extra_sub s WHERE s.clave = e.clave)")
    extras(refrescar=True)
    return mis_lugares(sub)


def borrar_de(sub: str) -> None:
    with pool().connection() as con:
        con.execute("DELETE FROM verif_extra_sub WHERE sub = %s", [sub])
        con.execute("DELETE FROM verif_extra e WHERE NOT EXISTS (SELECT 1 FROM verif_extra_sub s WHERE s.clave = e.clave)")
    extras(refrescar=True)


_ultimo_tramo: str | None = None


async def registrar_extras(obtener, cuota_estado, ahora: datetime | None = None, espera_s: float = 5) -> int:
    """A las horas de registro, consulta UNA vez cada lugar principal y extra (`obtener(lat, lon)` = resumen forzado, que registra lo previsto).
    Se salta el tramo entero si Open-Meteo va justo de cuota. Devuelve cuántos lugares consultó."""
    global _ultimo_tramo
    ahora = ahora or datetime.now(timezone.utc)
    tramo = ahora.strftime("%Y-%m-%dT%H")
    if ahora.hour not in HORAS_REGISTRO or ahora.minute >= 50 or tramo == _ultimo_tramo:
        return 0
    _ultimo_tramo = tramo
    est = cuota_estado()
    if est.get("frenado") or (est.get("porcentaje") or {}).get("dia", 0) >= CUOTA_MAX_PCT:
        log.warning("verif: se salta el registro de lugares extra (cuota de Open-Meteo al %s %%)", (est.get("porcentaje") or {}).get("dia"))
        return 0
    n = 0
    # también los principales: el ciclo de pregen (cada 2 h desde el arranque) no siempre cae en una hora de registro y quedaban días sin datos
    for e in [{"lat": p.lat, "lon": p.lon, "nombre": p.name} for p in PLACES] + list(extras(refrescar=True).values()):
        try:
            await obtener(e["lat"], e["lon"])
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("verif extra %s: %s", e["nombre"], ex)
        await asyncio.sleep(espera_s)
    return n


# ---------- informe mensual ----------
def _mes(mes: str | None, ahora: datetime) -> tuple[datetime, datetime, str]:
    if mes:
        ini = datetime.strptime(mes, "%Y-%m").replace(tzinfo=timezone.utc)
    else:
        ini = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fin = (ini + timedelta(days=32)).replace(day=1)
    return ini, fin, ini.strftime("%Y-%m")


def _ranking(con, lugar: str, ini: datetime, fin: datetime, lead_h: int, min_n: int) -> list[dict]:
    filas = con.execute("SELECT modelo, count(*) AS n, avg(abs(pred_c - obs_c)) AS mae, avg(pred_c - obs_c) AS sesgo, "
                        "avg((abs(pred_c - obs_c) <= 2)::int) AS dentro2 FROM verif_pred WHERE lugar = %s AND lead_h = %s AND obs_c IS NOT NULL "
                        "AND valida >= %s AND valida < %s GROUP BY modelo HAVING count(*) >= %s ORDER BY avg(abs(pred_c - obs_c))",
                        [lugar, lead_h, ini, fin, min_n]).fetchall()
    return [{"modelo": r["modelo"], "etiqueta": openmeteo.MODEL_LABELS.get(r["modelo"], r["modelo"]), "n": r["n"], "mae_c": round(r["mae"], 2),
             "sesgo_c": round(r["sesgo"], 2), "acierto_2c_pct": round(100 * r["dentro2"])} for r in filas]


def informe(lugares: list[str], mes: str | None = None, ahora: datetime | None = None, min_n: int = 4) -> dict:
    """Informe del mes (por defecto, el actual hasta hoy): por lugar, ranking de modelos a 24 h y 12 h, el ganador y cómo iba el mes anterior;
    y un resumen global (MAE medio ponderado por comparaciones). Solo lee la base de datos: no gasta ninguna consulta externa."""
    ahora = ahora or datetime.now(timezone.utc)
    ini, fin, etiqueta = _mes(mes, ahora)
    ini_prev = (ini - timedelta(days=1)).replace(day=1)
    out, glob = [], {}
    with pool().connection() as con:
        for lugar in lugares:
            c = coords(lugar)
            if not c:
                continue
            r24, r12 = _ranking(con, lugar, ini, fin, 24, min_n), _ranking(con, lugar, ini, fin, 12, min_n)
            prev = {x["modelo"]: x for x in _ranking(con, lugar, ini_prev, ini, 24, min_n)}
            dias = con.execute("SELECT count(DISTINCT date(valida)) AS d FROM verif_pred WHERE lugar = %s AND obs_c IS NOT NULL AND valida >= %s AND valida < %s",
                               [lugar, ini, fin]).fetchone()["d"]
            for x in r24:
                x["mae_mes_anterior_c"] = prev[x["modelo"]]["mae_c"] if x["modelo"] in prev else None
                g = glob.setdefault(x["modelo"], [0, 0.0, x["etiqueta"], 0])
                g[0] += x["n"]; g[1] += x["mae_c"] * x["n"]; g[3] += 1
            out.append({"lugar": lugar, "nombre": c[2], "lat": c[0], "lon": c[1], "principal": not lugar.startswith("x:"), "dias_con_datos": dias,
                        "a_24h": r24, "a_12h": r12, "ganador": r24[0]["etiqueta"] if r24 else None,
                        "nota": None if r24 else f"Aún no hay {min_n} comparaciones por modelo este mes (hacen falta estaciones oficiales a ≤ {RADIO_KM} km)."})
    general = sorted(({"modelo": m, "etiqueta": g[2], "n": g[0], "mae_c": round(g[1] / g[0], 2), "lugares": g[3]} for m, g in glob.items()), key=lambda x: x["mae_c"])
    return {"mes": etiqueta, "desde": ini.date().isoformat(), "hasta": min(fin, ahora).date().isoformat(), "lugares": out, "general": general,
            "ganador_general": general[0]["etiqueta"] if general else None,
            "metodo": f"Temperatura prevista por cada modelo a 12 y 24 h, comparada con la mediana de las estaciones oficiales (XEMA/METAR) a ≤ {RADIO_KM} km. "
                      f"MAE = error medio sin signo; sesgo > 0 = el modelo se pasa de caliente. Mínimo {min_n} comparaciones por modelo."}
