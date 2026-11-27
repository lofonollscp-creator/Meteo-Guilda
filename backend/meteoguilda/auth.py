"""Acceso a la web: toda radio.guildawork.com exige iniciar sesión con la cuenta de Guilda Work (OIDC contra Hydra), como La cesta.

El Caddy interno consulta /api/auth/check (forward_auth) antes de servir la web estática y /api/*; esta ruta mira la cookie de sesión.
Sin sesión: una navegación del navegador se redirige a /api/auth/login (→ Hydra → /api/auth/callback → sesión de 30 días) y el resto
de peticiones (fetch de la app) reciben 401. Públicas: /api/auth/*, /api/oauth/*, /.well-known/oauth-*, /api/health, /api/descargar/* (enlace firmado)
y /mcp (este valida su propio token).
El MCP y la API interna (red de Docker) no pasan por esta puerta.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import db
from .oauth import (COOKIE, _page, _fail, _pending, allowed_emails, enabled, hydra_profile, hydra_url, origin, rnd,
                    sha256, TX_SECONDS)

log = logging.getLogger("meteoguilda.auth")
router = APIRouter()

SESSION_COOKIE = "mg_session"
LOGIN_COOKIE = "mg_login"
SESSION_DAYS = 30
_cache: dict[str, tuple[float, dict]] = {}   # hash de sesión → (válido hasta, usuario); evita una consulta por cada fichero estático


def safe_return(value: str | None) -> str:
    """Solo rutas del propio sitio (evita redirecciones abiertas)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value if urlparse(value).netloc == "" else "/"


def session_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 200:
        return None
    h = sha256(token)
    now = time.time()
    hit = _cache.get(h)
    if hit and hit[0] > now:
        return hit[1]
    with db.pool().connection() as con:
        row = con.execute("SELECT sub, email FROM web_sessions WHERE token_hash=%s AND expires_at>now()", [h]).fetchone()
    if not row:
        _cache.pop(h, None)
        return None
    allowed = allowed_emails()
    if allowed and (row["email"] or "") not in allowed:
        return None
    if len(_cache) > 1000:
        _cache.clear()
    _cache[h] = (now + 30, row)
    return row


@router.get("/api/auth/login")
def login(request: Request):
    if not enabled():
        return _fail("El inicio de sesión no está configurado en este servidor.")
    tx, verifier = rnd(24), rnd(48)
    _pending[tx] = {"step": "weblogin", "verifier": verifier, "return_to": safe_return(request.query_params.get("return_to")),
                    "expires": time.time() + TX_SECONDS}
    r = RedirectResponse(hydra_url(tx, verifier, origin() + "/api/auth/callback"), status_code=302, headers={"cache-control": "no-store"})
    r.set_cookie(LOGIN_COOKIE, tx, max_age=TX_SECONDS, path="/api/auth", httponly=True, secure=True, samesite="lax")
    return r


@router.get("/api/auth/callback")
async def callback(request: Request):
    state = request.query_params.get("state", "")
    tx = _pending.pop(state, None)
    import hmac
    if (not tx or tx["step"] != "weblogin" or tx["expires"] < time.time() or not request.query_params.get("code")
            or not hmac.compare_digest(request.cookies.get(LOGIN_COOKIE, ""), state)):
        return _fail("El inicio de sesión ha caducado o no es tuyo. Vuelve a abrir radio.guildawork.com.")
    try:
        profile = await hydra_profile(request.app.state.http, request.query_params["code"], tx["verifier"], origin() + "/api/auth/callback")
    except Exception as e:  # noqa: BLE001
        log.warning("login: %s", e)
        return _fail("No se ha podido comprobar tu inicio de sesión. Vuelve a intentarlo.")
    email = (profile.get("email") or "").strip().lower()
    allowed = allowed_emails()
    if allowed and email not in allowed:
        log.warning("login: acceso denegado a %s", email)
        return _page(403, "Sin acceso", "<h1>Sin acceso</h1><p>Tu cuenta no tiene acceso a Radio Guilda. Pídelo a quien administra el servidor.</p>")
    token = rnd(32)
    with db.pool().connection() as con:
        con.execute("DELETE FROM web_sessions WHERE expires_at < now()")
        con.execute("INSERT INTO web_sessions(token_hash, sub, email, expires_at) VALUES (%s,%s,%s, now() + make_interval(days=>%s))",
                    [sha256(token), profile["sub"], email, SESSION_DAYS])
    r = RedirectResponse(tx["return_to"], status_code=302, headers={"cache-control": "no-store"})
    r.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, path="/", httponly=True, secure=True, samesite="lax")
    r.delete_cookie(LOGIN_COOKIE, path="/api/auth")
    return r


@router.get("/api/auth/check")
def check(request: Request):
    """Lo consulta el Caddy interno (forward_auth) con las cabeceras originales de la petición."""
    user = session_user(request)
    if user:
        return Response(status_code=200, headers={"x-user-email": user["email"] or "", "cache-control": "no-store"})
    h = request.headers
    navigation = h.get("sec-fetch-mode") == "navigate" or (h.get("sec-fetch-dest") == "document") or (
        "text/html" in h.get("accept", "") and "sec-fetch-mode" not in h)
    if navigation:
        original = h.get("x-forwarded-uri", "/")
        return RedirectResponse("/api/auth/login?return_to=" + quote(safe_return(original), safe=""), status_code=302,
                                headers={"cache-control": "no-store"})
    return JSONResponse({"detail": "Inicia sesión"}, status_code=401, headers={"cache-control": "no-store", "x-auth": "login"})


@router.get("/api/auth/me")
def me(request: Request):
    user = session_user(request)
    if not user:
        return JSONResponse({"detail": "Inicia sesión"}, status_code=401, headers={"cache-control": "no-store"})
    return {"email": user["email"]}


@router.api_route("/api/auth/logout", methods=["GET", "POST"], include_in_schema=False)
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        h = sha256(token)
        _cache.pop(h, None)
        with db.pool().connection() as con:
            con.execute("DELETE FROM web_sessions WHERE token_hash=%s", [h])
    r = _page(200, "Sesión cerrada", '<h1>Sesión cerrada</h1><p>Has salido de Radio Guilda en este dispositivo.</p><p><a href="/">Volver a entrar</a></p>')
    r.delete_cookie(SESSION_COOKIE, path="/")
    return r
