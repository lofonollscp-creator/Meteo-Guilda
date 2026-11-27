"""Descargas por enlace firmado: el MCP no devuelve ficheros ni respuestas enormes por la conversación, sino un enlace que caduca en una hora.

El servicio MCP (red interna, con MG_ADMIN_TOKEN) guarda el contenido con POST /api/descargas y recibe el enlace `/api/descargar/<token>`.
El token lleva el identificador del fichero y su caducidad firmados con HMAC-SHA256 (clave derivada de MG_SECRET_KEY o, sin ella, de
MG_ADMIN_TOKEN): no hace falta iniciar sesión para abrirlo, pero solo sirve para ese fichero y durante una hora (se puede abrir varias veces,
por si una descarga se corta). Los ficheros se borran solos a las 2 horas. Modelo: Guilda Work, app/descargas_enlace.py."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import db

router = APIRouter()
CADUCA_S = 3600
CONSERVAR_S = 2 * 3600
MAX_BYTES = 10 * 1024 * 1024


def _clave() -> bytes:
    base = os.environ.get("MG_SECRET_KEY") or os.environ.get("MG_ADMIN_TOKEN")
    if not base:
        raise HTTPException(503, "Sin clave del servidor no se pueden firmar enlaces")
    return hashlib.sha256(b"descarga-por-enlace|" + base.encode()).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def firmar(did: int, caduca: int) -> str:
    cuerpo = _b64(json.dumps([did, caduca], separators=(",", ":")).encode())
    return cuerpo + "." + _b64(hmac.new(_clave(), cuerpo.encode(), hashlib.sha256).digest()[:24])


def comprobar(token: str, ahora: float | None = None) -> int | None:
    """Identificador del fichero si la firma es buena y no ha caducado; si no, None."""
    try:
        cuerpo, firma = token.split(".", 1)
        if not hmac.compare_digest(firma, _b64(hmac.new(_clave(), cuerpo.encode(), hashlib.sha256).digest()[:24])):
            return None
        did, caduca = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except (ValueError, TypeError):
        return None
    return int(did) if (ahora or time.time()) <= caduca else None


class DescargaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    tipo: str = Field("application/octet-stream", max_length=100)
    contenido: str | None = None                      # texto (ADIF, JSON…)
    contenido_b64: str | None = None                  # binario
    sub: str | None = Field(None, max_length=200)


def crear(nombre: str, tipo: str, datos: bytes, sub: str | None = None) -> dict:
    if len(datos) > MAX_BYTES:
        raise HTTPException(413, f"El fichero pesa más de {MAX_BYTES // 1048576} MB")
    nombre = re.sub(r"[^\w.\- ]", "_", os.path.basename(nombre.replace("\\", "/")))[:150] or "fichero"
    with db.pool().connection() as con:
        con.execute("DELETE FROM descargas WHERE creada < now() - make_interval(secs => %s)", [CONSERVAR_S])
        did = con.execute("INSERT INTO descargas (sub, nombre, tipo, contenido) VALUES (%s,%s,%s,%s) RETURNING id", [sub, nombre, tipo, datos]).fetchone()["id"]
    base = os.environ.get("MG_PUBLIC_URL", "").rstrip("/")
    return {"enlace": f"{base}/api/descargar/{firmar(did, int(time.time()) + CADUCA_S)}", "nombre_archivo": nombre, "tipo_mime": tipo, "tamano": len(datos),
            "caduca_en_minutos": CADUCA_S // 60, "nota": "Enséñale el enlace a la persona para que descargue el fichero (caduca en una hora)."}


@router.post("/api/descargas", include_in_schema=False)
def guardar(b: DescargaIn, x_admin_token: str | None = Header(None)):
    """Solo para el servicio MCP (red interna): guarda el contenido y devuelve el enlace firmado."""
    esperado = os.environ.get("MG_ADMIN_TOKEN")
    if not esperado or not x_admin_token or not hmac.compare_digest(x_admin_token, esperado):
        raise HTTPException(403, "No autorizado")
    try:
        datos = base64.b64decode(b.contenido_b64) if b.contenido_b64 is not None else (b.contenido or "").encode("utf-8")
    except ValueError as e:
        raise HTTPException(422, "contenido_b64 no válido") from e
    return crear(b.nombre, b.tipo, datos, b.sub)


@router.get("/api/descargar/{token}")
def descargar(token: str):
    """Descarga por enlace firmado (sin sesión). 404 si el enlace es falso, ha caducado o el fichero ya no existe."""
    did = comprobar(token)
    fila = None
    if did is not None:
        with db.pool().connection() as con:
            fila = con.execute("SELECT nombre, tipo, contenido FROM descargas WHERE id = %s", [did]).fetchone()
    if not fila:
        raise HTTPException(404, "El enlace no existe o ha caducado (dura una hora): vuelve a pedir el fichero")
    return Response(bytes(fila["contenido"]), media_type=fila["tipo"], headers={
        "Content-Disposition": f'attachment; filename="{fila["nombre"]}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})
