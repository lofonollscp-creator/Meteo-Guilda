"""Avisos personalizados por umbral: la persona elige viento, racha, lluvia u oleaje para un lugar y recibe un push cuando la previsión de las
próximas 24 h lo supera. Se apoya en el mismo consentimiento, dispositivo y cola que el resto de notificaciones (push.py).

Privacidad: se guarda el dispositivo, la variable, el umbral y el lugar redondeado a ~1 km (igual que la ubicación de los avisos oficiales); se borra con
el dispositivo, al borrar la cuenta y sale en el acceso/portabilidad de datos. Como mucho 5 reglas por dispositivo y un aviso por regla y día."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, push
from .account import current

log = logging.getLogger("meteoguilda.reglas")
router = APIRouter()
MAX_REGLAS = 5
# variable → (nombre, unidad, mínimo, máximo del umbral)
VARIABLES = {"viento": ("Viento", "km/h", 10, 200), "racha": ("Rachas", "km/h", 20, 250), "lluvia": ("Lluvia en 24 h", "mm", 1, 300), "oleaje": ("Oleaje", "m", 0.3, 10)}


class ReglaIn(push.EndpointIn):
    variable: str = Field(max_length=10)
    umbral: float
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    nombre: str | None = Field(None, max_length=80)


def valor_previsto(variable: str, resumen: dict | None, mar: dict | None) -> tuple[float | None, str | None]:
    """(máximo previsto en las próximas 24 h, hora en que ocurre) para esa variable; (None, None) si no hay datos."""
    if variable == "oleaje":
        horas = [h for h in ((mar or {}).get("horas") or [])[:24] if h.get("ola_m") is not None]
        if not horas:
            return None, None
        h = max(horas, key=lambda x: x["ola_m"])
        return float(h["ola_m"]), h["hora"]
    pasos = ((resumen or {}).get("steps") or [])[:8]            # tramos de 3 h → 24 h
    if variable == "lluvia":
        v = [p.get("precipitation", {}).get("mean") for p in pasos]
        return (round(sum(x for x in v if x), 1), pasos[0]["time"]) if pasos else (None, None)
    var, k = ("wind_speed_10m", "mean") if variable == "viento" else ("wind_gusts_10m", "max")
    cand = [(p.get(var, {}).get(k), p["time"]) for p in pasos if p.get(var, {}).get(k) is not None]
    return max(cand, key=lambda x: x[0]) if cand else (None, None)


def mensaje(variable: str, valor: float, hora: str | None, umbral: float, nombre: str | None) -> tuple[str, str]:
    nom, unidad = VARIABLES[variable][0], VARIABLES[variable][1]
    lugar = f" · {nombre}" if nombre else ""
    cuando = f" hacia las {hora[11:16]}" if hora and variable != "lluvia" else ""
    return f"{nom}: previsión por encima de tu umbral{lugar}", f"Se prevén {valor:g} {unidad}{cuando} (tu umbral: {umbral:g} {unidad}). Próximas 24 h."


@router.post("/api/push/reglas/lista")
def lista(request: Request, b: push.EndpointIn):
    user = current(request)
    with db.pool().connection() as con:
        if not push._mia(con, user, b.endpoint):
            return {"reglas": [], "maximo": MAX_REGLAS}
        filas = con.execute("SELECT id, variable, umbral, lat, lon, nombre FROM push_reglas WHERE endpoint = %s ORDER BY id", [b.endpoint]).fetchall()
    return {"reglas": filas, "maximo": MAX_REGLAS, "variables": {k: {"nombre": v[0], "unidad": v[1], "min": v[2], "max": v[3]} for k, v in VARIABLES.items()}}


@router.put("/api/push/reglas")
def crear(request: Request, b: ReglaIn):
    user = current(request)
    if b.variable not in VARIABLES:
        raise HTTPException(422, "Variable no válida")
    nom, unidad, lo, hi = VARIABLES[b.variable]
    if not (lo <= b.umbral <= hi):
        raise HTTPException(422, f"El umbral de {nom.lower()} debe estar entre {lo:g} y {hi:g} {unidad}")
    lat, lon = round(b.lat, 2), round(b.lon, 2)
    with db.pool().connection() as con:
        if not push._mia(con, user, b.endpoint):
            raise HTTPException(404, "Este dispositivo no tiene las notificaciones activadas")
        previa = con.execute("SELECT id FROM push_reglas WHERE endpoint = %s AND variable = %s AND lat = %s AND lon = %s", [b.endpoint, b.variable, lat, lon]).fetchone()
        if not previa and con.execute("SELECT count(*) AS n FROM push_reglas WHERE endpoint = %s", [b.endpoint]).fetchone()["n"] >= MAX_REGLAS:
            raise HTTPException(409, f"Máximo {MAX_REGLAS} avisos personalizados por dispositivo; borra alguno antes")
        con.execute("INSERT INTO push_reglas (endpoint, variable, umbral, lat, lon, nombre) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (endpoint, variable, lat, lon) DO UPDATE SET umbral = EXCLUDED.umbral, nombre = EXCLUDED.nombre",
                    [b.endpoint, b.variable, b.umbral, lat, lon, (b.nombre or "").strip() or None])
    return {"ok": True}


@router.delete("/api/push/reglas/{rid}")
def borrar(request: Request, rid: int):
    user = current(request)
    with db.pool().connection() as con:
        n = con.execute("DELETE FROM push_reglas r USING push_subs s WHERE r.id = %s AND r.endpoint = s.endpoint AND s.sub = %s", [rid, user["sub"]]).rowcount
    if not n:
        raise HTTPException(404, "No existe ese aviso")
    return {"ok": True}


def de_persona(sub: str) -> list[dict]:
    """Para el acceso/portabilidad de datos."""
    with db.pool().connection() as con:
        return con.execute("SELECT r.variable, r.umbral, r.lat, r.lon, r.nombre FROM push_reglas r JOIN push_subs s USING (endpoint) WHERE s.sub = %s ORDER BY r.id", [sub]).fetchall()


async def revisar(obtener_resumen, obtener_mar, ahora: datetime | None = None) -> int:
    """Mira todas las reglas (una consulta por lugar) y encola un aviso por regla superada, como mucho uno al día. Devuelve cuántos encoló."""
    ahora = ahora or datetime.now(timezone.utc)
    with db.pool().connection() as con:
        reglas = con.execute("SELECT id, endpoint, variable, umbral, lat, lon, nombre FROM push_reglas").fetchall()
    por_lugar: dict[tuple, list[dict]] = defaultdict(list)
    for r in reglas:
        por_lugar[(r["lat"], r["lon"])].append(r)
    n = 0
    for (lat, lon), rs in por_lugar.items():
        resumen = mar = None
        try:
            if any(r["variable"] != "oleaje" for r in rs):
                resumen = await obtener_resumen(lat, lon)
            if any(r["variable"] == "oleaje" for r in rs):
                mar = await obtener_mar(lat, lon)
        except Exception as e:  # noqa: BLE001  (una fuente caída no debe parar las demás)
            log.warning("avisos personalizados %s,%s: %s", lat, lon, e)
            continue
        for r in rs:
            valor, hora = valor_previsto(r["variable"], resumen, mar)
            if valor is None or valor < r["umbral"]:
                continue
            titulo, cuerpo = mensaje(r["variable"], valor, hora, r["umbral"], r["nombre"])
            with db.pool().connection() as con:
                n += push.encolar(con, r["endpoint"], titulo, cuerpo, "/#/forecast", clave=f"regla:{r['id']}:{ahora:%Y-%m-%d}")
    return n
