"""Cuaderno conectado con LoTW (ARRL), eQSL y QRZ: subir los contactos e importar las confirmaciones.

- Las credenciales son de cada persona (su cuenta de Guilda Work), se guardan CIFRADAS con Fernet (MG_SECRET_KEY) y solo se introducen
  desde la web con su sesión: nunca por el MCP (como las claves de IA). La API no las devuelve jamás (solo el usuario y si están).
- Sin credenciales todo está desactivado: no se llama a ningún servicio.
- Subir y confirmar trabajan sobre el cuaderno de la clave (Authorization: Bearer <clave del cuaderno>) en nombre de la persona.
- Qué se ha subido y qué se ha confirmado queda en `qsos.servicios` ({"lotw": {"enviado": …, "confirmado": …}, …}); una confirmación marca
  también la QSL como recibida.

Servicios (ver docs/QSL-SERVICIOS.md):
  QRZ    API del Logbook (clave de API del logbook, de pago en QRZ): INSERT de un contacto por llamada y FETCH de los confirmados.
  eQSL   ImportADIF.cfm (usuario y contraseña) para subir y DownloadInBox.cfm para bajar las QSL recibidas.
  LoTW   lotwreport.adi (usuario y contraseña del sitio web) para las confirmaciones; para subir hace falta el certificado de la ARRL (.p12):
         se firma cada contacto como hace TQSL (fichero .tq8) y se envía a /lotw/upload. La firma es EXPERIMENTAL: compruébala con la primera subida
         real y, si LoTW la rechaza, exporta el ADIF y fírmalo con TQSL."""
from __future__ import annotations

import base64
import gzip
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import account, auth, db, logbook

log = logging.getLogger("meteoguilda.qsl")
router = APIRouter()

SERVICIOS = {"lotw": "LoTW (ARRL)", "eqsl": "eQSL", "qrz": "QRZ Logbook"}
URLS = {
    "qrz": "https://logbook.qrz.com/api",
    "eqsl_subir": "https://www.eqsl.cc/qslcard/ImportADIF.cfm",
    "eqsl_bajar": "https://www.eqsl.cc/qslcard/DownloadInBox.cfm",
    "eqsl_base": "https://www.eqsl.cc/",
    "lotw_bajar": "https://lotw.arrl.org/lotwuser/lotwreport.adi",
    "lotw_subir": "https://lotw.arrl.org/lotw/upload",
}
MAX_SUBIR = 300                 # por llamada (QRZ admite un contacto por petición)
VENTANA_MIN = 30                # una confirmación casa con un contacto si coinciden indicativo y banda y la hora difiere ≤ 30 min
QRZ_KEY = re.compile(r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$", re.I)


def cliente(request: Request) -> httpx.AsyncClient:
    """Cliente HTTP (en las pruebas se sustituye por servidores simulados)."""
    return request.app.state.http


# ---------- credenciales cifradas ----------
class CredIn(BaseModel):
    usuario: str | None = Field(None, max_length=60)
    password: str | None = Field(None, max_length=120)
    api_key: str | None = Field(None, max_length=40)
    qth_nick: str | None = Field(None, max_length=60)
    p12_b64: str | None = Field(None, max_length=40_000)       # certificado de la ARRL para firmar (solo LoTW, opcional)
    p12_password: str | None = Field(None, max_length=120)
    indicativo: str | None = Field(None, max_length=14)
    dxcc: int | None = Field(None, ge=1, le=999)
    grid: str | None = Field(None, max_length=6)
    cqz: int | None = Field(None, ge=1, le=40)
    ituz: int | None = Field(None, ge=1, le=90)


def _web(request: Request) -> dict:
    """Solo con la sesión de la web: las credenciales nunca viajan por el MCP ni con la identidad delegada."""
    u = auth.session_user(request)
    if u:
        return u
    if os.environ.get("MG_QA") and not request.headers.get("x-act-as"):
        return {"sub": "qa", "email": "qa@local"}
    raise HTTPException(403, "Las credenciales de LoTW, eQSL y QRZ solo se guardan desde la web, con tu sesión")


def _cargar(sub: str, servicio: str) -> tuple[dict | None, dict]:
    """(credenciales descifradas o None, fila pública)."""
    with db.pool().connection() as con:
        r = con.execute("SELECT cifrado, publico, estado FROM qsl_credenciales WHERE sub = %s AND servicio = %s", [sub, servicio]).fetchone()
    if not r:
        return None, {}
    txt = account.decrypt(r["cifrado"])
    return (json.loads(txt) if txt else None), {**r["publico"], **r["estado"]}


def _estado(sub: str, servicio: str, **kw) -> None:
    with db.pool().connection() as con:
        con.execute("UPDATE qsl_credenciales SET estado = estado || %s::jsonb WHERE sub = %s AND servicio = %s", [json.dumps(kw, default=str), sub, servicio])


def validar(servicio: str, b: CredIn) -> tuple[dict, dict]:
    """(secreto a cifrar, datos públicos). 422 con un texto claro si falta algo."""
    if servicio == "qrz":
        k = (b.api_key or "").strip().upper()
        if not QRZ_KEY.match(k):
            raise HTTPException(422, "La clave del logbook de QRZ tiene la forma XXXX-XXXX-XXXX-XXXX (QRZ → Logbook → Settings)")
        return {"api_key": k}, {"clave": "••••" + k[-4:]}
    usuario, pwd = (b.usuario or "").strip(), (b.password or "")
    if not usuario or not pwd:
        raise HTTPException(422, "Indica el usuario y la contraseña")
    if servicio == "eqsl":
        nick = (b.qth_nick or "").strip() or None
        return {"usuario": usuario, "password": pwd, "qth_nick": nick}, {"usuario": usuario, "qth_nick": nick}
    secreto, publico = {"usuario": usuario, "password": pwd}, {"usuario": usuario, "puede_subir": False}
    if b.p12_b64:
        try:
            p12 = base64.b64decode(b.p12_b64, validate=True)
            _cert_y_clave(p12, b.p12_password)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, "No se puede abrir el certificado .p12 (¿contraseña del certificado?)") from e
        call = (b.indicativo or "").strip().upper()
        grid = (b.grid or "").strip()
        if not logbook.CALL_RE.match(call) or not b.dxcc or not b.cqz or not b.ituz or not logbook.GRID_RE.match(grid):
            raise HTTPException(422, "Para subir a LoTW indica la estación: indicativo, entidad DXCC, locator, zona CQ y zona ITU (como en TQSL)")
        secreto.update(p12_b64=b.p12_b64, p12_password=b.p12_password or "", estacion={"call": call, "dxcc": b.dxcc, "grid": grid.upper()[:4], "cqz": b.cqz, "ituz": b.ituz})
        publico.update(puede_subir=True, indicativo=call, grid=grid.upper()[:4])
    return secreto, publico


@router.get("/api/qsl/servicios")
def servicios(request: Request):
    """Qué servicios tienes conectados (nunca devuelve las credenciales: solo el usuario o los 4 últimos caracteres) y su último resultado."""
    sub = account.current(request)["sub"]
    with db.pool().connection() as con:
        filas = {r["servicio"]: r for r in con.execute("SELECT servicio, publico, estado, actualizado FROM qsl_credenciales WHERE sub = %s", [sub]).fetchall()}
    out = {}
    for s, nombre in SERVICIOS.items():
        r = filas.get(s)
        out[s] = {"nombre": nombre, "conectado": bool(r), **((r["publico"] | r["estado"] | {"actualizado": r["actualizado"].isoformat()}) if r else {})}
    return {"servicios": out, "puede_guardar": account._fernet() is not None,
            "nota": "Las credenciales se guardan cifradas y solo desde la web. Subir y confirmar usan el cuaderno de tu clave de sincronización."}


@router.put("/api/qsl/servicios/{servicio}")
def guardar(request: Request, servicio: Literal["lotw", "eqsl", "qrz"], b: CredIn):
    """Guarda (cifradas) las credenciales de un servicio. SOLO desde la web con tu sesión."""
    user = _web(request)
    f = account._fernet()
    if not f:
        raise HTTPException(503, "El servidor no puede guardar credenciales (falta MG_SECRET_KEY)")
    secreto, publico = validar(servicio, b)
    with db.pool().connection() as con:
        con.execute("INSERT INTO qsl_credenciales (sub, servicio, cifrado, publico) VALUES (%s,%s,%s,%s) ON CONFLICT (sub, servicio) DO UPDATE SET "
                    "cifrado = EXCLUDED.cifrado, publico = EXCLUDED.publico, estado = '{}'::jsonb, actualizado = now()",
                    [user["sub"], servicio, f.encrypt(json.dumps(secreto).encode()).decode(), json.dumps(publico)])
    return servicios(request)


@router.delete("/api/qsl/servicios/{servicio}")
def quitar(request: Request, servicio: Literal["lotw", "eqsl", "qrz"]):
    """Borra las credenciales de un servicio (solo desde la web). Lo ya subido o confirmado en el cuaderno se queda."""
    user = _web(request)
    with db.pool().connection() as con:
        con.execute("DELETE FROM qsl_credenciales WHERE sub = %s AND servicio = %s", [user["sub"], servicio])
    return servicios(request)


def borrar_de(sub: str) -> None:
    with db.pool().connection() as con:
        con.execute("DELETE FROM qsl_credenciales WHERE sub = %s", [sub])


def de_persona(sub: str) -> list[dict]:
    with db.pool().connection() as con:
        return [{"servicio": r["servicio"], **r["publico"], "credenciales": "guardadas cifradas (no se exportan)"}
                for r in con.execute("SELECT servicio, publico FROM qsl_credenciales WHERE sub = %s", [sub]).fetchall()]


# ---------- ADIF ----------
_CAMPO = re.compile(r"<([A-Za-z_0-9]+):(\d+)(?::[A-Za-z])?>")


def leer_adif(texto: str) -> list[dict]:
    """Registros de un ADIF tal cual (campos en mayúsculas), sin validar: para casar confirmaciones."""
    m = re.search(r"<eoh>", texto, re.I)
    cuerpo = texto[m.end():] if m else texto
    out = []
    for rec in re.split(r"<eor>", cuerpo, flags=re.I):
        d, pos = {}, 0
        while (mm := _CAMPO.search(rec, pos)):
            n = int(mm.group(2))
            d[mm.group(1).upper()] = rec[mm.end():mm.end() + n]
            pos = mm.end() + n
        if d.get("CALL"):
            out.append(d)
    return out


def _modo_adif(q: dict) -> str:
    return (q.get("mode") or "").upper().replace("-", "") if q.get("mode") not in (None, "Otro") else ""


def registro(q: dict) -> str:
    """Un contacto en ADIF (sin cabecera)."""
    def f(k, v):
        return f"<{k}:{len(str(v))}>{v}" if v not in (None, "") else ""
    d = q["qso_at"].astimezone(timezone.utc)
    return "".join([f("CALL", q["call"]), f("QSO_DATE", d.strftime("%Y%m%d")), f("TIME_ON", d.strftime("%H%M%S")), f("BAND", (q.get("band") or "").upper()),
                    f("MODE", _modo_adif(q)), f("FREQ", q.get("freq_mhz")), f("RST_SENT", q.get("rst_sent")), f("RST_RCVD", q.get("rst_rcvd")),
                    f("GRIDSQUARE", q.get("grid")), "<EOR>"])


def _fecha_adif(d: dict) -> datetime | None:
    try:
        tm = (d.get("TIME_ON") or "0000").ljust(6, "0")[:6]
        return datetime.strptime(d["QSO_DATE"] + tm, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def casar(h: str, servicio: str, registros: list[dict], ahora: datetime) -> tuple[int, int]:
    """Marca como confirmados los contactos del cuaderno que casan con las confirmaciones. Devuelve (nuevas, sin_pareja)."""
    nuevas = sin = 0
    with db.pool().connection() as con:
        for d in registros:
            t = _fecha_adif(d)
            if not t:
                sin += 1
                continue
            band = (d.get("BAND") or "").lower() or None
            fila = con.execute("SELECT id, servicios FROM qsos WHERE key_hash = %s AND upper(call) = %s AND qso_at BETWEEN %s AND %s "
                               "AND (%s::text IS NULL OR band IS NULL OR lower(band) = %s) ORDER BY abs(extract(epoch FROM qso_at - %s)) LIMIT 1",
                               [h, d["CALL"].upper(), t - timedelta(minutes=VENTANA_MIN), t + timedelta(minutes=VENTANA_MIN), band, band, t]).fetchone()
            if not fila:
                sin += 1
                continue
            if (fila["servicios"].get(servicio) or {}).get("confirmado"):
                continue
            con.execute("UPDATE qsos SET qsl_rcvd = TRUE, servicios = jsonb_set(servicios, %s, coalesce(servicios->%s, '{}'::jsonb) || %s::jsonb) WHERE id = %s",
                        [[servicio], servicio, json.dumps({"confirmado": ahora.isoformat()}), fila["id"]])
            nuevas += 1
    return nuevas, sin


def pendientes(h: str, servicio: str, limite: int = MAX_SUBIR) -> list[dict]:
    with db.pool().connection() as con:
        return con.execute("SELECT id, call, qso_at, band, mode, freq_mhz, rst_sent, rst_rcvd, grid FROM qsos WHERE key_hash = %s "
                           "AND NOT (servicios ? %s AND servicios->%s ? 'enviado') ORDER BY qso_at LIMIT %s", [h, servicio, servicio, limite]).fetchall()


def marcar_enviados(ids: list[int], servicio: str, ahora: datetime) -> None:
    if not ids:
        return
    with db.pool().connection() as con:
        con.execute("UPDATE qsos SET servicios = jsonb_set(servicios, %s, coalesce(servicios->%s, '{}'::jsonb) || %s::jsonb) WHERE id = ANY(%s)",
                    [[servicio], servicio, json.dumps({"enviado": ahora.isoformat()}), ids])


# ---------- QRZ ----------
async def qrz_subir(http: httpx.AsyncClient, cred: dict, qsos: list[dict]) -> tuple[list[int], list[str]]:
    ok, errores = [], []
    for q in qsos:
        r = await http.post(URLS["qrz"], data={"KEY": cred["api_key"], "ACTION": "INSERT", "ADIF": registro(q)}, timeout=30)
        res = {k: v[0] for k, v in parse_qs(r.text.strip()).items()}
        if res.get("RESULT") == "OK" or "duplicate" in (res.get("REASON") or "").lower():
            ok.append(q["id"])
        elif res.get("RESULT") == "AUTH" or "invalid api key" in (res.get("REASON") or "").lower():
            raise PermissionError("QRZ no acepta la clave del logbook (¿caducada o sin suscripción?)")
        else:
            errores.append(f"{q['call']}: {res.get('REASON') or r.text[:80]}")
    return ok, errores


async def qrz_confirmaciones(http: httpx.AsyncClient, cred: dict, desde: datetime | None) -> list[dict]:
    opcion = "TYPE:ADIF,STATUS:CONFIRMED" + (f",MODSINCE:{desde:%Y-%m-%d}" if desde else "")
    r = await http.post(URLS["qrz"], data={"KEY": cred["api_key"], "ACTION": "FETCH", "OPTION": opcion}, timeout=60)
    res = {k: v[0] for k, v in parse_qs(r.text.strip(), keep_blank_values=True).items()}
    if res.get("RESULT") == "AUTH":
        raise PermissionError("QRZ no acepta la clave del logbook")
    if res.get("RESULT") != "OK":
        if "no log entries" in (res.get("REASON") or "").lower() or res.get("COUNT") == "0":
            return []
        raise RuntimeError(f"QRZ: {res.get('REASON') or 'respuesta inesperada'}")
    return leer_adif(html.unescape(res.get("ADIF", "")))


# ---------- eQSL ----------
async def eqsl_subir(http: httpx.AsyncClient, cred: dict, qsos: list[dict]) -> tuple[list[int], list[str]]:
    cab = f"<EQSL_USER:{len(cred['usuario'])}>{cred['usuario']}<EQSL_PSWD:{len(cred['password'])}>{cred['password']}"
    if cred.get("qth_nick"):
        cab += f"<APP_EQSL_QTH_NICKNAME:{len(cred['qth_nick'])}>{cred['qth_nick']}"
    adif = "Radio Guilda\n" + cab + "<PROGRAMID:11>RadioGuilda<EOH>\n" + "\n".join(registro(q) for q in qsos)
    r = await http.post(URLS["eqsl_subir"], data={"ADIFData": adif}, timeout=60)
    txt = re.sub(r"<[^>]+>", " ", r.text)
    if re.search(r"Error:\s*No match on eQSL_User", txt, re.I) or re.search(r"Error:.*(password|user)", txt, re.I):
        raise PermissionError("eQSL no acepta el usuario o la contraseña")
    m = re.search(r"Result:\s*(\d+)\s+out of\s+(\d+)\s+records added", txt, re.I)
    if not m:
        raise RuntimeError("eQSL no ha confirmado la subida: " + " ".join(txt.split())[:160])
    dup = len(re.findall(r"Duplicate", txt, re.I))
    errores = [" ".join(x.split())[:120] for x in re.findall(r"(?:Error|Warning):[^\n]*", txt) if "duplicate" not in x.lower()][:10]
    if int(m.group(1)) + dup >= len(qsos):
        return [q["id"] for q in qsos], errores
    return [], errores or [f"eQSL aceptó {m.group(1)} de {m.group(2)}"]


async def eqsl_confirmaciones(http: httpx.AsyncClient, cred: dict, desde: datetime | None) -> list[dict]:
    p = {"UserName": cred["usuario"], "Password": cred["password"]}
    if desde:
        p["RcvdSince"] = desde.strftime("%Y%m%d%H%M")
    if cred.get("qth_nick"):
        p["QTHNickname"] = cred["qth_nick"]
    r = await http.get(URLS["eqsl_bajar"], params=p, timeout=60)
    if re.search(r"no log entries|no qsls", r.text, re.I):
        return []
    m = re.search(r'HREF="(?:\.\./)?(downloadedfiles/[^"]+\.adi)"', r.text, re.I)
    if not m:
        if re.search(r"Error:", r.text, re.I):
            raise PermissionError("eQSL: " + " ".join(re.sub(r"<[^>]+>", " ", r.text).split())[:140])
        raise RuntimeError("eQSL no ha devuelto el fichero de QSL recibidas")
    f = await http.get(URLS["eqsl_base"] + m.group(1), timeout=60)
    f.raise_for_status()
    return leer_adif(f.text)


# ---------- LoTW ----------
def _cert_y_clave(p12: bytes, password: str | None):
    from cryptography.hazmat.primitives.serialization import pkcs12
    clave, cert, _ = pkcs12.load_key_and_certificates(p12, password.encode() if password else None)
    if clave is None or cert is None:
        raise ValueError("El .p12 no trae certificado y clave privada")
    return cert, clave


def _gabbi(k: str, v: str) -> str:
    return f"<{k}:{len(v)}>{v}\n"


def lotw_band(b: str | None) -> str:
    return (b or "").upper()


def firmar_tq8(cred: dict, qsos: list[dict]) -> bytes:
    """Fichero .tq8 (GABBI comprimido) como el de TQSL: certificado, estación y cada contacto firmado (RSA-SHA1 sobre SIGNDATA).
    SIGNDATA = campos de la estación por orden alfabético (CQZ, GRIDSQUARE, ITUZ) + BAND, CALL, FREQ, MODE, QSO_DATE, QSO_TIME, en mayúsculas."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import Encoding
    cert, clave = _cert_y_clave(base64.b64decode(cred["p12_b64"]), cred.get("p12_password"))
    e = cred["estacion"]
    out = [_gabbi("TQSL_IDENT", "RadioGuilda LoTW 1.0 (formato TQSL GABBI)"), "\n",
           "<Rec_Type:5>tCERT\n", _gabbi("CERT_UID", "1"), _gabbi("CERTIFICATE", base64.encodebytes(cert.public_bytes(Encoding.DER)).decode()), "<eor>\n\n",
           "<Rec_Type:8>tSTATION\n", _gabbi("STATION_UID", "1"), _gabbi("CERT_UID", "1"), _gabbi("CALL", e["call"]), _gabbi("DXCC", str(e["dxcc"])),
           _gabbi("GRIDSQUARE", e["grid"]), _gabbi("CQZ", str(e["cqz"])), _gabbi("ITUZ", str(e["ituz"])), "<eor>\n\n"]
    est = f"{e['cqz']}{e['grid']}{e['ituz']}"
    for q in qsos:
        d = q["qso_at"].astimezone(timezone.utc)
        campos = {"BAND": lotw_band(q.get("band")), "CALL": q["call"].upper(), "FREQ": f"{q['freq_mhz']:g}" if q.get("freq_mhz") else "",
                  "MODE": _modo_adif(q), "QSO_DATE": d.strftime("%Y-%m-%d"), "QSO_TIME": d.strftime("%H:%M:%SZ")}
        signdata = (est + "".join(campos[k] for k in ("BAND", "CALL", "FREQ", "MODE", "QSO_DATE", "QSO_TIME"))).upper()
        firma = base64.encodebytes(clave.sign(signdata.encode(), padding.PKCS1v15(), hashes.SHA1())).decode()
        out += ["<Rec_Type:8>tCONTACT\n", _gabbi("STATION_UID", "1")] + [_gabbi(k, v) for k, v in campos.items() if v]
        out += [f"<SIGN_LOTW_V2.0:{len(firma)}:6>{firma}\n", _gabbi("SIGNDATA", signdata), "<eor>\n\n"]
    return gzip.compress("".join(out).encode())


async def lotw_subir(http: httpx.AsyncClient, cred: dict, qsos: list[dict]) -> tuple[list[int], list[str]]:
    if not cred.get("p12_b64"):
        raise PermissionError("Para subir a LoTW añade en la web tu certificado de la ARRL (.p12) y los datos de la estación")
    tq8 = firmar_tq8(cred, qsos)
    r = await http.post(URLS["lotw_subir"], files={"upfile": ("radioguilda.tq8", tq8, "application/octet-stream")}, timeout=120)
    if ".UPL. accepted" in r.text:
        return [q["id"] for q in qsos], []
    msg = re.search(r"<!--\s*\.UPLMESSAGE\.\s*(.*?)-->", r.text, re.S)
    return [], [("LoTW rechazó el fichero: " + (msg.group(1).strip() if msg else f"código {r.status_code}"))[:200]]


async def lotw_confirmaciones(http: httpx.AsyncClient, cred: dict, desde: datetime | None) -> list[dict]:
    p = {"login": cred["usuario"], "password": cred["password"], "qso_query": "1", "qso_qsl": "yes", "qso_qsldetail": "yes",
         "qso_qslsince": (desde or datetime(1990, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%d")}
    r = await http.get(URLS["lotw_bajar"], params=p, timeout=120)
    if not re.search(r"<eoh>", r.text, re.I):
        raise PermissionError("LoTW no acepta el usuario o la contraseña del sitio web")
    return [d for d in leer_adif(r.text) if (d.get("QSL_RCVD") or "Y").upper() == "Y"]


SUBIR = {"qrz": qrz_subir, "eqsl": eqsl_subir, "lotw": lotw_subir}
BAJAR = {"qrz": qrz_confirmaciones, "eqsl": eqsl_confirmaciones, "lotw": lotw_confirmaciones}


def _cuaderno(authorization: str | None = Header(None)) -> str:
    k = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
    h = logbook.auth(k)
    if not h:
        raise HTTPException(401, "Falta la clave del cuaderno (Authorization: Bearer <clave>)")
    return h


def _cred(request: Request, servicio: str) -> tuple[str, dict, dict]:
    sub = account.current(request)["sub"]
    cred, pub = _cargar(sub, servicio)
    if not cred:
        raise HTTPException(409, f"{SERVICIOS[servicio]} no está conectado: añade tus credenciales en la web (Radio → Cuaderno → Servicios)")
    return sub, cred, pub


@router.post("/api/qsl/{servicio}/subir")
async def subir(request: Request, servicio: Literal["lotw", "eqsl", "qrz"], h: str = Depends(_cuaderno)):
    """Sube al servicio los contactos del cuaderno que aún no se le han enviado (hasta 300 por llamada)."""
    sub, cred, _ = _cred(request, servicio)
    lista = pendientes(h, servicio)
    if not lista:
        return {"enviados": 0, "pendientes": 0, "errores": [], "mensaje": "No hay contactos pendientes de subir"}
    ahora = datetime.now(timezone.utc)
    try:
        ok, errores = await SUBIR[servicio](cliente(request), cred, lista)
    except PermissionError as e:
        _estado(sub, servicio, ultimo_error=str(e), ultimo_error_en=ahora.isoformat())
        raise HTTPException(409, str(e)) from e
    except (httpx.HTTPError, RuntimeError) as e:
        _estado(sub, servicio, ultimo_error=str(e)[:200], ultimo_error_en=ahora.isoformat())
        raise HTTPException(502, f"{SERVICIOS[servicio]} no responde: {str(e)[:120]}") from e
    marcar_enviados(ok, servicio, ahora)
    _estado(sub, servicio, ultima_subida=ahora.isoformat(), subidos_ultima=len(ok), ultimo_error=errores[0] if errores else None)
    return {"enviados": len(ok), "con_error": len(lista) - len(ok), "errores": errores[:10], "pendientes": len(pendientes(h, servicio))}


@router.post("/api/qsl/{servicio}/confirmaciones")
async def confirmaciones(request: Request, servicio: Literal["lotw", "eqsl", "qrz"], desde_cero: bool = False, h: str = Depends(_cuaderno)):
    """Descarga las confirmaciones (QSL recibidas) desde la última vez (o todas con `desde_cero`) y las marca en el cuaderno."""
    sub, cred, pub = _cred(request, servicio)
    ahora = datetime.now(timezone.utc)
    desde = None if desde_cero or not pub.get("ultima_descarga") else datetime.fromisoformat(pub["ultima_descarga"]) - timedelta(days=1)
    try:
        regs = await BAJAR[servicio](cliente(request), cred, desde)
    except PermissionError as e:
        _estado(sub, servicio, ultimo_error=str(e), ultimo_error_en=ahora.isoformat())
        raise HTTPException(409, str(e)) from e
    except (httpx.HTTPError, RuntimeError) as e:
        _estado(sub, servicio, ultimo_error=str(e)[:200], ultimo_error_en=ahora.isoformat())
        raise HTTPException(502, f"{SERVICIOS[servicio]} no responde: {str(e)[:120]}") from e
    nuevas, sin = casar(h, servicio, regs, ahora)
    _estado(sub, servicio, ultima_descarga=ahora.isoformat(), confirmadas_ultima=nuevas, ultimo_error=None)
    return {"descargadas": len(regs), "confirmados_nuevos": nuevas, "sin_pareja": sin}
