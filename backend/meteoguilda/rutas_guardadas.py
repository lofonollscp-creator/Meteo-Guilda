"""Rutas guardadas de cada persona (en su cuenta de Guilda Work): nombre, modo (a pie / coche) y de 2 a 10 puntos de paso.

Las usan las alertas en ruta (alertas.py) y el planificador de salidas por tramos (salidas.py). Las coordenadas se guardan con 4 decimales (~10 m):
son las que la persona eligió en el mapa. Se borran al borrar la cuenta y salen en la descarga de datos."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import db
from .account import current

router = APIRouter()
MAX_RUTAS = 20


class PuntoIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    nombre: str | None = Field(None, max_length=80)


class RutaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    modo: Literal["foot", "car", "sea"] = "foot"
    waypoints: list[PuntoIn] = Field(min_length=2, max_length=10)


def _limpia(w: list[PuntoIn]) -> list[dict]:
    return [{"lat": round(p.lat, 4), "lon": round(p.lon, 4), **({"nombre": p.nombre.strip()} if p.nombre and p.nombre.strip() else {})} for p in w]


def de_persona(sub: str) -> list[dict]:
    with db.pool().connection() as con:
        filas = con.execute("SELECT id, nombre, modo, waypoints, creada FROM rutas_guardadas WHERE sub = %s ORDER BY id", [sub]).fetchall()
    return [{**f, "creada": f["creada"].isoformat()} for f in filas]


def una(sub: str, rid: int) -> dict | None:
    with db.pool().connection() as con:
        return con.execute("SELECT id, nombre, modo, waypoints FROM rutas_guardadas WHERE id = %s AND sub = %s", [rid, sub]).fetchone()


def borrar_de(sub: str) -> int:
    with db.pool().connection() as con:
        return con.execute("DELETE FROM rutas_guardadas WHERE sub = %s", [sub]).rowcount


@router.get("/api/rutas")
def lista(request: Request):
    """Tus rutas guardadas."""
    return {"rutas": de_persona(current(request)["sub"]), "maximo": MAX_RUTAS}


@router.post("/api/rutas", status_code=201)
def guardar(request: Request, b: RutaIn):
    """Guarda una ruta (nombre, modo y puntos de paso). Con el mismo nombre, la sustituye."""
    sub = current(request)["sub"]
    nombre = b.nombre.strip()
    with db.pool().connection() as con:
        previa = con.execute("SELECT id FROM rutas_guardadas WHERE sub = %s AND lower(nombre) = lower(%s)", [sub, nombre]).fetchone()
        if previa:
            con.execute("UPDATE rutas_guardadas SET modo = %s, waypoints = %s WHERE id = %s", [b.modo, json.dumps(_limpia(b.waypoints)), previa["id"]])
            return {"id": previa["id"], "sustituida": True}
        if con.execute("SELECT count(*) AS n FROM rutas_guardadas WHERE sub = %s", [sub]).fetchone()["n"] >= MAX_RUTAS:
            raise HTTPException(409, f"Máximo {MAX_RUTAS} rutas guardadas; borra alguna antes")
        rid = con.execute("INSERT INTO rutas_guardadas (sub, nombre, modo, waypoints) VALUES (%s,%s,%s,%s) RETURNING id",
                          [sub, nombre, b.modo, json.dumps(_limpia(b.waypoints))]).fetchone()["id"]
    return {"id": rid, "sustituida": False}


@router.get("/api/rutas/{rid}")
def ver(request: Request, rid: int):
    r = una(current(request)["sub"], rid)
    if not r:
        raise HTTPException(404, "No existe esa ruta")
    return r


@router.delete("/api/rutas/{rid}")
def borrar(request: Request, rid: int):
    """Borra una ruta guardada (y las alertas que dependían de ella)."""
    sub = current(request)["sub"]
    with db.pool().connection() as con:
        n = con.execute("DELETE FROM rutas_guardadas WHERE id = %s AND sub = %s", [rid, sub]).rowcount
        if n:
            con.execute("DELETE FROM alertas WHERE sub = %s AND tipo = 'ruta' AND (params->>'ruta_id')::bigint = %s", [sub, rid])
    if not n:
        raise HTTPException(404, "No existe esa ruta")
    return {"ok": True}
