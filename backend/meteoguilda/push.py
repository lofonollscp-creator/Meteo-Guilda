"""Notificaciones push (Web Push con VAPID): avisos oficiales (AEMET) del lugar elegido y recordatorios de pasos de satélite.

Privacidad (RGPD): solo con consentimiento explícito de la persona (permiso del navegador + activarlo en Ajustes); se guarda el mínimo (el «endpoint»
del navegador, sus claves de cifrado, una ubicación redondeada a ~1 km y qué avisos quiere); el contenido viaja cifrado de extremo a extremo hasta el
navegador y la clave VAPID solo lleva la URL pública del sitio (nunca un correo ni datos personales). Se borra al desactivar, al borrar la cuenta
(ver account.delete_me) o tras 90 días sin entregas correctas. Los endpoints solo pueden ser de los servicios de push conocidos (evita SSRF)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import avisos as avisos_mod
from . import db
from .account import current

log = logging.getLogger("meteoguilda.push")
router = APIRouter()

VAPID_SUB = "https://radio.guildawork.com"
SERVICIOS = (".googleapis.com", ".push.services.mozilla.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com")
MAX_DISPOSITIVOS = 5
MAX_PENDIENTES = 10
SIN_ENTREGAS_DIAS = 90
B64 = re.compile(r"^[A-Za-z0-9_\-]{8,200}={0,2}$")


def disponible() -> bool:
    return bool(os.environ.get("MG_VAPID_PRIVATE_KEY") and os.environ.get("MG_VAPID_PUBLIC_KEY"))


def endpoint_ok(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (u.port in (None, 443)) and len(url) <= 600 and not re.fullmatch(r"[\d.:]+", host) and any(host.endswith(s) for s in SERVICIOS)


class SubIn(BaseModel):
    endpoint: str = Field(max_length=600)
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    nombre: str | None = Field(None, max_length=80)
    avisos: bool = True
    nivel_min: int = Field(2, ge=1, le=3)
    pasos: bool = True


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=600)


class PasoIn(EndpointIn):
    satelite: str = Field(max_length=60)
    aos: str = Field(max_length=40)
    aos_az: float | None = None
    max_el: float | None = None


@router.get("/api/push/config")
def config(request: Request):
    current(request)
    return {"disponible": disponible(), "clave_publica": os.environ.get("MG_VAPID_PUBLIC_KEY") if disponible() else None, "max_dispositivos": MAX_DISPOSITIVOS}


@router.put("/api/push/suscripcion")
def suscribir(request: Request, b: SubIn):
    user = current(request)
    if not disponible():
        raise HTTPException(503, "Las notificaciones no están configuradas en este servidor")
    if not endpoint_ok(b.endpoint) or not B64.match(b.p256dh) or not B64.match(b.auth):
        raise HTTPException(422, "Suscripción no válida")
    lat = round(b.lat, 2) if b.lat is not None else None
    lon = round(b.lon, 2) if b.lon is not None else None
    with db.pool().connection() as con:
        n = con.execute("SELECT count(*) AS n FROM push_subs WHERE sub = %s AND endpoint <> %s", [user["sub"], b.endpoint]).fetchone()["n"]
        if n >= MAX_DISPOSITIVOS:
            raise HTTPException(409, f"Máximo {MAX_DISPOSITIVOS} dispositivos; desactiva alguno antes")
        con.execute("INSERT INTO push_subs (endpoint, sub, p256dh, auth, lat, lon, nombre, avisos, nivel_min, pasos) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (endpoint) DO UPDATE SET sub=EXCLUDED.sub, p256dh=EXCLUDED.p256dh, auth=EXCLUDED.auth, lat=EXCLUDED.lat, lon=EXCLUDED.lon, "
                    "nombre=EXCLUDED.nombre, avisos=EXCLUDED.avisos, nivel_min=EXCLUDED.nivel_min, pasos=EXCLUDED.pasos",
                    [b.endpoint, user["sub"], b.p256dh, b.auth, lat, lon, (b.nombre or "").strip() or None, b.avisos, b.nivel_min, b.pasos])
    return {"ok": True}


@router.post("/api/push/estado")
def estado(request: Request, b: EndpointIn):
    user = current(request)
    with db.pool().connection() as con:
        r = con.execute("SELECT lat, lon, nombre, avisos, nivel_min, pasos FROM push_subs WHERE endpoint = %s AND sub = %s", [b.endpoint, user["sub"]]).fetchone()
    return {"suscrito": bool(r), **(r or {})}


@router.delete("/api/push/suscripcion")
def desuscribir(request: Request, b: EndpointIn):
    user = current(request)
    with db.pool().connection() as con:
        con.execute("DELETE FROM push_subs WHERE endpoint = %s AND sub = %s", [b.endpoint, user["sub"]])
    return {"ok": True}


def _mia(con, user: dict, endpoint: str) -> bool:
    return bool(con.execute("SELECT 1 FROM push_subs WHERE endpoint = %s AND sub = %s", [endpoint, user["sub"]]).fetchone())


def encolar(con, endpoint: str, titulo: str, cuerpo: str, url: str = "/", cuando: datetime | None = None, clave: str | None = None) -> bool:
    cur = con.execute("INSERT INTO push_cola (endpoint, cuando, titulo, cuerpo, url, clave) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      [endpoint, cuando or datetime.now(timezone.utc), titulo[:120], cuerpo[:300], url, clave])
    return cur.rowcount > 0


@router.post("/api/push/paso")
def recordar_paso(request: Request, b: PasoIn):
    """Recordatorio del servidor 5 min antes de un paso de satélite (suena aunque la pestaña esté cerrada)."""
    user = current(request)
    try:
        aos = datetime.fromisoformat(b.aos.replace("Z", "+00:00"))
        aos = aos if aos.tzinfo else aos.replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise HTTPException(422, "Hora no válida") from e
    ahora = datetime.now(timezone.utc)
    if not (ahora - timedelta(minutes=4) < aos < ahora + timedelta(days=3)):
        raise HTTPException(422, "Ese paso ya ha empezado o queda demasiado lejos")
    with db.pool().connection() as con:
        if not _mia(con, user, b.endpoint):
            raise HTTPException(404, "Este dispositivo no tiene las notificaciones activadas")
        if con.execute("SELECT count(*) AS n FROM push_cola WHERE endpoint = %s AND enviada IS NULL", [b.endpoint]).fetchone()["n"] >= MAX_PENDIENTES:
            raise HTTPException(409, "Demasiados avisos pendientes")
        ok = encolar(con, b.endpoint, "Paso de satélite", f"{b.satelite}: pasa en 5 min" + (f" (máx. {round(b.max_el)}°)" if b.max_el is not None else ""), "/#/radio/satelites",
                     max(ahora, aos - timedelta(minutes=5)), "paso:" + hashlib.sha1(f"{b.satelite}{aos.isoformat()}".encode()).hexdigest()[:16])
    return {"ok": True, "nuevo": ok, "aviso_a": (aos - timedelta(minutes=5)).isoformat()}


@router.post("/api/push/prueba")
async def prueba(request: Request, b: EndpointIn):
    user = current(request)
    with db.pool().connection() as con:
        s = con.execute("SELECT * FROM push_subs WHERE endpoint = %s AND sub = %s", [b.endpoint, user["sub"]]).fetchone()
    if not s:
        raise HTTPException(404, "Este dispositivo no tiene las notificaciones activadas")
    ok = await enviar(s, {"titulo": "Notificaciones activadas", "cuerpo": "Así te avisaremos de los avisos oficiales y de los pasos de satélite.", "url": "/"})
    return {"ok": ok}


# ---------- envío ----------
def _webpush(s: dict, payload: dict) -> int:
    """Envía una notificación. Devuelve el código HTTP del servicio de push (201 = aceptada)."""
    from pywebpush import WebPushException, webpush
    try:
        r = webpush({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=os.environ["MG_VAPID_PRIVATE_KEY"], vapid_claims={"sub": VAPID_SUB}, ttl=3600, timeout=15)
        return r.status_code
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0


async def enviar(s: dict, payload: dict) -> bool:
    try:
        code = await asyncio.to_thread(_webpush, s, payload)
    except Exception as e:  # noqa: BLE001
        log.warning("push: %s", e)
        return False
    with db.pool().connection() as con:
        if code in (404, 410):                          # el navegador ya no existe o se dio de baja: se borra
            con.execute("DELETE FROM push_subs WHERE endpoint = %s", [s["endpoint"]])
        elif 200 <= code < 300:
            con.execute("UPDATE push_subs SET ultimo_ok = now() WHERE endpoint = %s", [s["endpoint"]])
    return 200 <= code < 300


async def enviar_pendientes(ahora: datetime | None = None) -> int:
    ahora = ahora or datetime.now(timezone.utc)
    with db.pool().connection() as con:
        filas = con.execute("SELECT c.id, c.titulo, c.cuerpo, c.url, c.clave, s.* FROM push_cola c JOIN push_subs s USING (endpoint) "
                            "WHERE c.enviada IS NULL AND c.cuando <= %s AND c.cuando > %s ORDER BY c.cuando LIMIT 50", [ahora, ahora - timedelta(hours=2)]).fetchall()
    n = 0
    for f in filas:
        ok = await enviar(f, {"titulo": f["titulo"], "cuerpo": f["cuerpo"], "url": f["url"], "tag": f["clave"] or "mg"})
        with db.pool().connection() as con:
            con.execute("UPDATE push_cola SET enviada = %s WHERE id = %s", [ahora, f["id"]])    # un solo intento: avisos viejos no se reenvían
        n += ok
    return n


async def encolar_avisos(http: httpx.AsyncClient) -> int:
    """Pone en cola los avisos oficiales nuevos (nivel elegido o superior) de la ubicación de cada dispositivo. Sin repetir: clave única por aviso."""
    lista = await avisos_mod.todos(http)
    if lista is None:
        return 0
    with db.pool().connection() as con:
        subs = con.execute("SELECT endpoint, lat, lon, nombre, nivel_min FROM push_subs WHERE avisos AND lat IS NOT NULL").fetchall()
    n = 0
    for s in subs:
        for a in avisos_mod.para_punto(lista, s["lat"], s["lon"])["avisos"]:
            if a["nivel"] < s["nivel_min"]:
                continue
            clave = "aviso:" + hashlib.sha1(f"{a['evento']}|{a['zona']}|{a['desde']}|{a['nivel']}".encode()).hexdigest()[:16]
            hasta = a.get("hasta") or ""
            with db.pool().connection() as con:
                n += encolar(con, s["endpoint"], f"Aviso {a['color']}: {a['evento']}", f"{a['zona']}" + (f" · hasta {hasta[11:16]}" if len(hasta) >= 16 else "") + (f" · {s['nombre']}" if s["nombre"] else ""),
                             "/#/forecast", clave=clave)
    return n


def limpiar(ahora: datetime | None = None) -> None:
    ahora = ahora or datetime.now(timezone.utc)
    with db.pool().connection() as con:
        con.execute("DELETE FROM push_cola WHERE cuando < %s", [ahora - timedelta(days=2)])
        con.execute("DELETE FROM push_subs WHERE ultimo_ok < %s", [ahora - timedelta(days=SIN_ENTREGAS_DIAS)])


def borrar_de(sub: str) -> int:
    """Borra todos los dispositivos de una persona (derecho de supresión)."""
    with db.pool().connection() as con:
        return con.execute("DELETE FROM push_subs WHERE sub = %s", [sub]).rowcount


def datos_de(sub: str) -> list[dict]:
    """Lo que se guarda de sus dispositivos, para el acceso/portabilidad (sin las claves de cifrado)."""
    with db.pool().connection() as con:
        filas = con.execute("SELECT endpoint, lat, lon, nombre, avisos, nivel_min, pasos, creada, ultimo_ok FROM push_subs WHERE sub = %s ORDER BY creada", [sub]).fetchall()
    return [{"servicio_push": urlparse(f["endpoint"]).hostname, "ubicacion_aprox": [f["lat"], f["lon"]] if f["lat"] is not None else None, "nombre_lugar": f["nombre"],
             "avisos_oficiales": f["avisos"], "nivel_minimo": f["nivel_min"], "pasos_satelite": f["pasos"], "alta": f["creada"].isoformat(), "ultima_entrega": f["ultimo_ok"].isoformat()} for f in filas]


async def bucle(http: httpx.AsyncClient, datos=None) -> None:
    """Cada 30 s envía lo pendiente; cada 15 min mira avisos nuevos; cada hora limpia."""
    t = 0
    while True:
        try:
            if disponible():
                await enviar_pendientes()
                if t % 30 == 0:
                    await encolar_avisos(http)
                if t % 120 == 0:
                    await asyncio.to_thread(limpiar)
                if datos and t % 60 == 7:                              # cada 30 min: avisos personalizados por umbral (ver reglas.py)
                    from . import reglas
                    await reglas.revisar(*datos)
                if t % 20 == 10:                                       # cada 10 min: ¿algo falla en silencio? (ver vigilante.py)
                    from . import vigilante
                    await asyncio.to_thread(vigilante.revisar)
        except Exception as e:  # noqa: BLE001
            log.warning("bucle push: %s", e)
        t += 1
        await asyncio.sleep(30)
