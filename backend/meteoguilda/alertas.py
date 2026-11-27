"""Alertas para radioaficionado y salidas, sobre las notificaciones push que ya existen (push.py).

Cada persona (su cuenta de Guilda Work) elige qué le interesa y le llega a TODOS sus dispositivos con las notificaciones activadas:
  - tropo:    apertura de propagación troposférica VHF/UHF (super-refracción o conducto) en las próximas 24 h en un punto (su QTH).
  - hf:       mejora de HF (flujo solar alto y Kp bajo) o, si lo prefiere, tormenta geomagnética (Kp alto).
  - satelite: aviso N minutos antes de cada paso de los satélites que elija (con elevación mínima) sobre su QTH.
  - meteo:    aviso oficial (AEMET) o un umbral propio (viento, racha, lluvia, oleaje) en su QTH u otro punto.
  - ruta:     lo mismo, pero en los puntos de una de sus rutas guardadas (rutas_guardadas.py).

Límites que pone la persona: un tope de avisos al día (entre todas sus alertas) y un horario de silencio (en su zona horaria); lo que cae en el
silencio no se manda y se vuelve a mirar después. Además, cada alerta avisa como mucho una vez al día (los pasos de satélite, una vez por paso).
Coste: se revisan cada 15 min solo las alertas de personas con algún dispositivo; las consultas se agrupan por lugar y se reutilizan las cachés
de la API (resumen 2 h, tropo 3 h, NOAA 15 min, avisos de AEMET 15 min).

Privacidad: se guardan el tipo, sus parámetros (lugar redondeado a ~1 km) y qué se envió (para el tope y no repetir; 30 días). Se borra con la
cuenta y sale en la descarga de datos."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from . import avisos as avisos_mod
from . import db, push, reglas, rutas_guardadas
from .account import current

log = logging.getLogger("meteoguilda.alertas")
router = APIRouter()

MAX_ALERTAS = 12
MAX_RUTA = 3
GUARDAR_ENVIOS_DIAS = 30
NIVELES_TROPO = {"normal": 0, "sub-refracción": 0, "super-refracción": 1, "conducto": 2}
VARIABLES_METEO = {**{k: v for k, v in reglas.VARIABLES.items()}, "aviso": ("Aviso oficial", "nivel", 1, 3)}
COLOR = {1: "amarillo", 2: "naranja", 3: "rojo"}
TIPOS = {
    "tropo": "Apertura tropo VHF/UHF",
    "hf": "Condiciones de HF",
    "satelite": "Pasos de satélite",
    "meteo": "Tiempo en un lugar",
    "ruta": "Tiempo en una ruta guardada",
}


# ---------- parámetros de cada tipo ----------
class _Lugar(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    nombre: str | None = Field(None, max_length=80)


class TropoP(_Lugar):
    nivel: Literal["super-refracción", "conducto"] = "super-refracción"


class HfP(BaseModel):
    modo: Literal["mejora", "tormenta"] = "mejora"
    sfi_min: float = Field(120, ge=60, le=300)
    kp_max: float = Field(3, ge=0, le=9)
    kp_min: float = Field(5, ge=1, le=9)


class SateliteP(_Lugar):
    norad: list[int] = Field(min_length=1, max_length=5)
    min_el: float = Field(20, ge=0, le=90)
    antelacion_min: int = Field(10, ge=1, le=60)


class MeteoP(_Lugar):
    variable: Literal["viento", "racha", "lluvia", "oleaje", "aviso"]
    umbral: float


class RutaP(BaseModel):
    ruta_id: int
    variable: Literal["viento", "racha", "lluvia", "oleaje", "aviso"]
    umbral: float


MODELOS = {"tropo": TropoP, "hf": HfP, "satelite": SateliteP, "meteo": MeteoP, "ruta": RutaP}


class AlertaIn(BaseModel):
    tipo: Literal["tropo", "hf", "satelite", "meteo", "ruta"]
    nombre: str | None = Field(None, max_length=80)
    params: dict = Field(default_factory=dict)


class AlertaPatch(BaseModel):
    activa: bool | None = None
    nombre: str | None = Field(None, max_length=80)


class PrefsIn(BaseModel):
    max_dia: int | None = Field(None, ge=1, le=30)
    silencio_desde: int | None = Field(None, ge=0, le=23)
    silencio_hasta: int | None = Field(None, ge=0, le=23)
    zona: str | None = Field(None, max_length=60)
    sin_silencio: bool = False


def validar(tipo: str, params: dict, sub: str | None = None) -> dict:
    """Parámetros limpios de una alerta (422 con un texto claro si no valen)."""
    try:
        p = MODELOS[tipo](**params).model_dump()
    except ValidationError as e:
        err = e.errors()[0]
        raise HTTPException(422, f"Parámetro «{'.'.join(map(str, err['loc'])) or tipo}» no válido: {err['msg']}") from e
    if "lat" in p:
        p["lat"], p["lon"] = round(p["lat"], 2), round(p["lon"], 2)
        p["nombre"] = (p.get("nombre") or "").strip() or None
    if tipo in ("meteo", "ruta"):
        nom, unidad, lo, hi = VARIABLES_METEO[p["variable"]]
        if not (lo <= p["umbral"] <= hi):
            raise HTTPException(422, f"El umbral de {nom.lower()} debe estar entre {lo:g} y {hi:g} {unidad}")
    if tipo == "ruta" and sub is not None and not rutas_guardadas.una(sub, p["ruta_id"]):
        raise HTTPException(404, "No existe esa ruta guardada")
    if tipo == "satelite":
        p["norad"] = sorted(set(p["norad"]))
    return p


# ---------- preferencias: tope diario y silencio ----------
DEF_PREFS = {"max_dia": 6, "silencio_desde": None, "silencio_hasta": None, "zona": "Europe/Madrid"}


def prefs_de(sub: str) -> dict:
    with db.pool().connection() as con:
        r = con.execute("SELECT max_dia, silencio_desde, silencio_hasta, zona FROM alertas_prefs WHERE sub = %s", [sub]).fetchone()
    return {**DEF_PREFS, **(r or {})}


def _zona(p: dict) -> ZoneInfo:
    try:
        return ZoneInfo(p.get("zona") or "Europe/Madrid")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Madrid")


def en_silencio(p: dict, cuando: datetime) -> bool:
    d, h = p.get("silencio_desde"), p.get("silencio_hasta")
    if d is None or h is None or d == h:
        return False
    hora = cuando.astimezone(_zona(p)).hour
    return d <= hora < h if d < h else (hora >= d or hora < h)        # 23→7 cruza la medianoche


def inicio_del_dia(p: dict, ahora: datetime) -> datetime:
    local = ahora.astimezone(_zona(p))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def enviadas_hoy(sub: str, p: dict, ahora: datetime) -> int:
    with db.pool().connection() as con:
        return con.execute("SELECT count(*) AS n FROM alertas_envios WHERE sub = %s AND cuando >= %s", [sub, inicio_del_dia(p, ahora)]).fetchone()["n"]


# ---------- fuentes de datos (inyectables en las pruebas) ----------
@dataclass
class Fuentes:
    resumen: Callable[[float, float], Awaitable[dict]]
    mar: Callable[[float, float], Awaitable[dict]]
    tropo: Callable[[float, float], Awaitable[dict]]
    solar: Callable[[], Awaitable[dict]]
    pasos: Callable[[float, float, list[int], float, float], Awaitable[list[dict]]]
    avisos: Callable[[], Awaitable[list[dict] | None]]


class _Memo:
    """Una ronda de revisión: cada consulta (mismo lugar, misma fuente) se hace una sola vez."""

    def __init__(self, f: Fuentes):
        self.f, self.c = f, {}

    async def get(self, nombre: str, *args):
        k = (nombre, *[tuple(a) if isinstance(a, list) else a for a in args])
        if k not in self.c:
            try:
                self.c[k] = ("ok", await getattr(self.f, nombre)(*args))
            except Exception as e:  # noqa: BLE001  (una fuente caída no para las demás alertas)
                log.warning("alertas: fuente %s%s: %s", nombre, args, e)
                self.c[k] = ("error", e)
        estado, v = self.c[k]
        if estado == "error":
            raise RuntimeError(f"Fuente {nombre} no disponible")
        return v


def _hora_local(iso: str, p: dict) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(_zona(p)).strftime("%H:%M")
    except ValueError:
        return iso[11:16]


def _rumbo(az: float | None) -> str:
    if az is None:
        return ""
    return ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][int(((az % 360) + 22.5) // 45) % 8]


async def _valor_meteo(memo: _Memo, variable: str, lat: float, lon: float) -> tuple[float | None, str | None, dict | None]:
    """(valor, hora, aviso) en un punto: para 'aviso', el aviso oficial de mayor nivel; si no, el máximo previsto en 24 h."""
    if variable == "aviso":
        lista = await memo.get("avisos")
        if lista is None:
            return None, None, None
        av = avisos_mod.para_punto(lista, lat, lon)["avisos"]
        return (float(av[0]["nivel"]), av[0].get("desde"), av[0]) if av else (0.0, None, None)
    if variable == "oleaje":
        v, h = reglas.valor_previsto(variable, None, await memo.get("mar", lat, lon))
    else:
        v, h = reglas.valor_previsto(variable, await memo.get("resumen", lat, lon), None)
    return v, h, None


def _puntos_ruta(w: list[dict], n: int = 4) -> list[dict]:
    """Hasta n puntos repartidos (siempre el primero y el último) para no gastar consultas de más."""
    if len(w) <= n:
        return w
    idx = sorted({round(i * (len(w) - 1) / (n - 1)) for i in range(n)})
    return [w[i] for i in idx]


async def evaluar(a: dict, memo: _Memo, p: dict, ahora: datetime, sub: str | None = None) -> list[dict]:
    """Avisos que tocaría mandar ahora para una alerta: [{clave, titulo, cuerpo, url, cuando, valor}]. No envía nada."""
    t, q, aid = a["tipo"], a["params"], a["id"]
    dia = ahora.astimezone(_zona(p)).strftime("%Y-%m-%d")
    lugar = f" · {q['nombre']}" if q.get("nombre") else ""
    if t == "tropo":
        serie = (await memo.get("tropo", q["lat"], q["lon"])).get("serie") or []
        objetivo = NIVELES_TROPO[q["nivel"]]
        buenas = [s for s in serie[:24] if NIVELES_TROPO.get(s["cat"], 0) >= objetivo]
        if not buenas:
            return []
        mejor = min(buenas, key=lambda s: s["gradient"])
        return [{"clave": f"tropo:{aid}:{dia}", "titulo": f"Apertura tropo VHF/UHF prevista{lugar}", "valor": mejor["gradient"],
                 "cuerpo": f"{mejor['cat'].capitalize()} hacia las {mejor['time'][11:16]} (gradiente {mejor['gradient']} N/km). Indicador orientativo de los modelos.",
                 "url": "/#/radio", "cuando": ahora}]
    if t == "hf":
        s = await memo.get("solar")
        kp, sfi = s.get("kp"), s.get("sfi")
        if q["modo"] == "tormenta":
            if kp is None or kp < q["kp_min"]:
                return []
            return [{"clave": f"hf:{aid}:{dia}", "titulo": f"Tormenta geomagnética: Kp {kp:g}", "valor": kp, "url": "/#/radio", "cuando": ahora,
                     "cuerpo": "HF degradado, sobre todo en rutas polares; buen momento para VHF (posible aurora en el norte)."}]
        if kp is None or sfi is None or sfi < q["sfi_min"] or kp > q["kp_max"]:
            return []
        return [{"clave": f"hf:{aid}:{dia}", "titulo": "Buenas condiciones de HF", "valor": sfi, "url": "/#/radio", "cuando": ahora,
                 "cuerpo": f"Flujo solar {sfi:g} y Kp {kp:g}: " + ("buenas aperturas en 15, 12 y 10 m." if sfi >= 150 else "20 m fiable y 15 m a ratos.")}]
    if t == "satelite":
        pasos = await memo.get("pasos", q["lat"], q["lon"], q["norad"], 3.0, q["min_el"])
        out = []
        for ps in pasos:
            aos = datetime.fromisoformat(ps["aos"])
            if aos <= ahora + timedelta(minutes=1):
                continue
            cuando = max(ahora, aos - timedelta(minutes=q["antelacion_min"]))
            out.append({"clave": f"sat:{aid}:{ps['norad']}:{aos:%Y%m%d%H%M}", "titulo": f"Paso de {ps['satelite']} a las {_hora_local(ps['aos'], p)}",
                        "cuerpo": f"Máx. {ps['max_el']}° · sale por el {_rumbo(ps.get('aos_az'))} · {round(ps['duracion_s'] / 60)} min{lugar}",
                        "url": "/#/radio/satelites", "cuando": cuando, "valor": ps["max_el"]})
        return out
    if t == "meteo":
        v, hora, av = await _valor_meteo(memo, q["variable"], q["lat"], q["lon"])
        if v is None or v < q["umbral"]:
            return []
        if q["variable"] == "aviso":
            return [{"clave": f"meteo:{aid}:{dia}:{av['evento']}", "titulo": f"Aviso {COLOR.get(av['nivel'], 'amarillo')}: {av['evento']}{lugar}",
                     "cuerpo": f"{av['zona']}" + (f" · hasta {_hora_local(av['hasta'], p)}" if av.get("hasta") else "") + ". Fuente: AEMET.",
                     "url": "/#/forecast", "cuando": ahora, "valor": v}]
        titulo, cuerpo = reglas.mensaje(q["variable"], v, hora, q["umbral"], q.get("nombre"))
        return [{"clave": f"meteo:{aid}:{dia}", "titulo": titulo, "cuerpo": cuerpo, "url": "/#/forecast", "cuando": ahora, "valor": v}]
    if t == "ruta":
        ruta = rutas_guardadas.una(sub, q["ruta_id"]) if sub else None
        if not ruta:
            return []
        peor = None
        for i, w in enumerate(_puntos_ruta(ruta["waypoints"])):
            try:
                v, hora, av = await _valor_meteo(memo, q["variable"], round(w["lat"], 2), round(w["lon"], 2))
            except RuntimeError:
                continue
            if v is not None and (peor is None or v > peor[0]):
                peor = (v, hora, av, w.get("nombre") or ("salida" if i == 0 else f"punto {i + 1}"))
        if not peor or peor[0] < q["umbral"]:
            return []
        v, hora, av, donde = peor
        nom, unidad = VARIABLES_METEO[q["variable"]][:2]
        if q["variable"] == "aviso":
            cuerpo = f"Aviso {COLOR.get(av['nivel'], 'amarillo')} de {av['evento']} cerca de {donde} ({av['zona']}). Fuente: AEMET."
        else:
            cuerpo = f"Se prevén {v:g} {unidad} cerca de {donde}" + (f" hacia las {hora[11:16]}" if hora and q["variable"] != "lluvia" else "") + f" (tu umbral: {q['umbral']:g} {unidad}). Próximas 24 h."
        return [{"clave": f"ruta:{aid}:{dia}", "titulo": f"Ruta «{ruta['nombre']}»: {nom.lower()} por encima de tu umbral", "cuerpo": cuerpo,
                 "url": "/#/routes", "cuando": ahora, "valor": v}]
    return []


def entregar(sub: str, a: dict, d: dict, p: dict, ahora: datetime, endpoints: list[str]) -> str:
    """Pone el aviso en la cola de cada dispositivo de la persona. Devuelve 'ok', 'silencio', 'tope', 'repetida' o 'sin_dispositivos'."""
    if not endpoints:
        return "sin_dispositivos"
    if en_silencio(p, d["cuando"]):
        return "silencio"
    with db.pool().connection() as con:
        with con.transaction():
            if con.execute("SELECT 1 FROM alertas_envios WHERE sub = %s AND clave = %s", [sub, d["clave"]]).fetchone():
                return "repetida"
            if con.execute("SELECT count(*) AS n FROM alertas_envios WHERE sub = %s AND cuando >= %s", [sub, inicio_del_dia(p, ahora)]).fetchone()["n"] >= p["max_dia"]:
                return "tope"
            con.execute("INSERT INTO alertas_envios (sub, clave, cuando, titulo) VALUES (%s,%s,%s,%s)", [sub, d["clave"], ahora, d["titulo"][:120]])
            for e in endpoints:
                push.encolar(con, e, d["titulo"], d["cuerpo"], d["url"], d["cuando"], "al:" + d["clave"][:80])
            con.execute("UPDATE alertas SET ultima = %s WHERE id = %s", [ahora, a["id"]])
    return "ok"


async def revisar(f: Fuentes, ahora: datetime | None = None) -> dict:
    """Revisa todas las alertas activas de personas con algún dispositivo. Devuelve el recuento por resultado."""
    ahora = ahora or datetime.now(timezone.utc)
    with db.pool().connection() as con:
        filas = con.execute("SELECT a.id, a.sub, a.tipo, a.nombre, a.params FROM alertas a WHERE a.activa AND EXISTS (SELECT 1 FROM push_subs s WHERE s.sub = a.sub) "
                            "ORDER BY a.sub, a.id").fetchall()
        devs: dict[str, list[str]] = {}
        for r in con.execute("SELECT sub, endpoint FROM push_subs WHERE sub IN (SELECT DISTINCT sub FROM alertas WHERE activa)").fetchall():
            devs.setdefault(r["sub"], []).append(r["endpoint"])
        con.execute("DELETE FROM alertas_envios WHERE cuando < %s", [ahora - timedelta(days=GUARDAR_ENVIOS_DIAS)])
    memo, cuenta, prefs = _Memo(f), {}, {}
    for a in filas:
        p = prefs.get(a["sub"]) or prefs.setdefault(a["sub"], await asyncio.to_thread(prefs_de, a["sub"]))
        try:
            disparos = await evaluar(a, memo, p, ahora, a["sub"])
        except Exception as e:  # noqa: BLE001
            log.warning("alerta %s: %s", a["id"], e)
            cuenta["error"] = cuenta.get("error", 0) + 1
            continue
        for d in disparos:
            r = await asyncio.to_thread(entregar, a["sub"], a, d, p, ahora, devs.get(a["sub"], []))
            cuenta[r] = cuenta.get(r, 0) + 1
    return cuenta


# ---------- API ----------
def _vista(a: dict) -> dict:
    return {"id": a["id"], "tipo": a["tipo"], "tipo_texto": TIPOS.get(a["tipo"], a["tipo"]), "nombre": a["nombre"], "params": a["params"], "activa": a["activa"],
            "ultima": a["ultima"].isoformat() if a.get("ultima") else None}


def de_persona(sub: str) -> dict:
    with db.pool().connection() as con:
        filas = con.execute("SELECT * FROM alertas WHERE sub = %s ORDER BY id", [sub]).fetchall()
        env = con.execute("SELECT clave, cuando, titulo FROM alertas_envios WHERE sub = %s ORDER BY cuando DESC LIMIT 20", [sub]).fetchall()
        n = con.execute("SELECT count(*) AS n FROM push_subs WHERE sub = %s", [sub]).fetchone()["n"]
    return {"alertas": [_vista(a) for a in filas], "ultimos_envios": [{"titulo": e["titulo"], "cuando": e["cuando"].isoformat()} for e in env], "dispositivos": n}


def catalogo() -> dict:
    return {"tipos": TIPOS, "variables": {k: {"nombre": v[0], "unidad": v[1], "min": v[2], "max": v[3]} for k, v in VARIABLES_METEO.items()},
            "niveles_tropo": ["super-refracción", "conducto"], "maximo": MAX_ALERTAS, "maximo_ruta": MAX_RUTA,
            "parametros": {"tropo": "lat, lon, nombre?, nivel (super-refracción | conducto)", "hf": "modo (mejora | tormenta), sfi_min (60-300), kp_max (0-9), kp_min (1-9, para tormenta)",
                           "satelite": "norad (1-5 identificadores), lat, lon, nombre?, min_el (0-90°), antelacion_min (1-60)",
                           "meteo": "lat, lon, nombre?, variable (viento | racha | lluvia | oleaje | aviso), umbral (aviso: nivel 1-3)",
                           "ruta": "ruta_id (de /api/rutas), variable, umbral"}}


@router.get("/api/alertas")
def lista(request: Request):
    """Tus alertas de radio y salidas, tus límites (tope diario y silencio), lo último enviado y cuántos dispositivos las reciben."""
    sub = current(request)["sub"]
    p = prefs_de(sub)
    return {**de_persona(sub), "prefs": p, "enviadas_hoy": enviadas_hoy(sub, p, datetime.now(timezone.utc)), **catalogo()}


@router.put("/api/alertas/prefs")
def poner_prefs(request: Request, b: PrefsIn):
    """Tope de avisos al día (1-30) y horario de silencio (horas 0-23 en tu zona; `sin_silencio=true` lo quita)."""
    sub = current(request)["sub"]
    p = prefs_de(sub)
    if b.zona is not None:
        try:
            ZoneInfo(b.zona)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HTTPException(422, "Zona horaria desconocida (p. ej. Europe/Madrid)") from e
        p["zona"] = b.zona
    if b.max_dia is not None:
        p["max_dia"] = b.max_dia
    if b.sin_silencio:
        p["silencio_desde"] = p["silencio_hasta"] = None
    elif b.silencio_desde is not None or b.silencio_hasta is not None:
        if b.silencio_desde is None or b.silencio_hasta is None:
            raise HTTPException(422, "Indica el principio y el final del silencio")
        p["silencio_desde"], p["silencio_hasta"] = b.silencio_desde, b.silencio_hasta
    with db.pool().connection() as con:
        con.execute("INSERT INTO alertas_prefs (sub, max_dia, silencio_desde, silencio_hasta, zona) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (sub) DO UPDATE SET "
                    "max_dia = EXCLUDED.max_dia, silencio_desde = EXCLUDED.silencio_desde, silencio_hasta = EXCLUDED.silencio_hasta, zona = EXCLUDED.zona, actualizada = now()",
                    [sub, p["max_dia"], p["silencio_desde"], p["silencio_hasta"], p["zona"]])
    return {"prefs": p}


@router.post("/api/alertas", status_code=201)
def crear(request: Request, b: AlertaIn):
    """Crea una alerta. `params` según el tipo (ver `parametros` en GET /api/alertas)."""
    sub = current(request)["sub"]
    params = validar(b.tipo, b.params, sub)
    with db.pool().connection() as con:
        filas = con.execute("SELECT tipo FROM alertas WHERE sub = %s", [sub]).fetchall()
        if len(filas) >= MAX_ALERTAS:
            raise HTTPException(409, f"Máximo {MAX_ALERTAS} alertas; borra alguna antes")
        if b.tipo == "ruta" and sum(f["tipo"] == "ruta" for f in filas) >= MAX_RUTA:
            raise HTTPException(409, f"Máximo {MAX_RUTA} alertas de ruta (cada una consulta varios puntos)")
        a = con.execute("INSERT INTO alertas (sub, tipo, nombre, params) VALUES (%s,%s,%s,%s) RETURNING *",
                        [sub, b.tipo, (b.nombre or "").strip() or None, json.dumps(params)]).fetchone()
    return _vista(a)


@router.patch("/api/alertas/{aid}")
def cambiar(request: Request, aid: int, b: AlertaPatch):
    """Pausa o reanuda (`activa`) o renombra una alerta."""
    sub = current(request)["sub"]
    with db.pool().connection() as con:
        a = con.execute("UPDATE alertas SET activa = coalesce(%s, activa), nombre = CASE WHEN %s THEN %s ELSE nombre END WHERE id = %s AND sub = %s RETURNING *",
                        [b.activa, b.nombre is not None, (b.nombre or "").strip() or None, aid, sub]).fetchone()
    if not a:
        raise HTTPException(404, "No existe esa alerta")
    return _vista(a)


@router.delete("/api/alertas/{aid}")
def borrar(request: Request, aid: int):
    sub = current(request)["sub"]
    with db.pool().connection() as con:
        if not con.execute("DELETE FROM alertas WHERE id = %s AND sub = %s", [aid, sub]).rowcount:
            raise HTTPException(404, "No existe esa alerta")
    return {"ok": True}


_comprobaciones: dict[str, list[float]] = {}


@router.get("/api/alertas/{aid}/comprobar")
async def comprobar(request: Request, aid: int):
    """Evalúa ahora una alerta SIN mandar nada: si saltaría, con qué texto y si lo frenaría el silencio o el tope. Como mucho 20 al hora por persona."""
    sub = current(request)["sub"]
    q = [t for t in _comprobaciones.get(sub, []) if time.time() - t < 3600]
    if len(q) >= 20:
        raise HTTPException(429, "Demasiadas comprobaciones; inténtalo más tarde")
    _comprobaciones[sub] = [*q, time.time()]
    with db.pool().connection() as con:
        a = con.execute("SELECT * FROM alertas WHERE id = %s AND sub = %s", [aid, sub]).fetchone()
    if not a:
        raise HTTPException(404, "No existe esa alerta")
    f: Fuentes | None = getattr(request.app.state, "fuentes_alertas", None)
    if f is None:
        raise HTTPException(503, "Las alertas no están activas en este servidor")
    p, ahora = prefs_de(sub), datetime.now(timezone.utc)
    try:
        ds = await evaluar(a, _Memo(f), p, ahora, sub)
    except RuntimeError as e:
        return {"saltaria": False, "error": str(e)}
    hoy = enviadas_hoy(sub, p, ahora)
    return {"saltaria": bool(ds), "avisos": [{"titulo": d["titulo"], "cuerpo": d["cuerpo"], "cuando": d["cuando"].isoformat(), "en_silencio": en_silencio(p, d["cuando"])} for d in ds],
            "enviadas_hoy": hoy, "tope": p["max_dia"], "tope_alcanzado": hoy >= p["max_dia"]}


def borrar_de(sub: str) -> None:
    with db.pool().connection() as con:
        con.execute("DELETE FROM alertas WHERE sub = %s", [sub])
        con.execute("DELETE FROM alertas_prefs WHERE sub = %s", [sub])
        con.execute("DELETE FROM alertas_envios WHERE sub = %s", [sub])


def exportar_de(sub: str) -> dict[str, Any]:
    return {**de_persona(sub), "prefs": prefs_de(sub)}

