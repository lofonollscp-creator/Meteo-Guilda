"""OAuth 2.1 para el MCP: el propio servicio hace de servidor de autorización (registro dinámico de clientes, código + PKCE S256,
renovación con rotación) y la persona se identifica con su cuenta de Guilda Work (OIDC contra el Hydra de PRE/PRO).

Flujo: el cliente MCP (Claude, etc.) → /api/oauth/authorize → login en Hydra → /api/oauth/callback → pantalla de consentimiento
→ /api/oauth/consent → el cliente canjea el código en /api/oauth/token. El servicio MCP valida los tokens con /api/oauth/introspect.
Los códigos y las transacciones pendientes viven en memoria (la API corre con un solo proceso); clientes y tokens, en PostgreSQL.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import db

log = logging.getLogger("meteoguilda.oauth")
router = APIRouter()

SCOPE = "mcp"
ACCESS_SECONDS = 3600
REFRESH_DAYS = 90
CODE_SECONDS = 300
TX_SECONDS = 600
MAX_CLIENTS = 500
REGISTERS_PER_HOUR = 30
COOKIE = "mg_oauth"
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "content-type, authorization",
        "access-control-allow-methods": "GET, POST, OPTIONS"}

_pending: dict[str, dict] = {}   # transacción en curso (login en Hydra / consentimiento) → datos
_codes: dict[str, dict] = {}     # hash del código de autorización → datos (un solo uso)
_registrations: list[float] = []


# ---------- configuración ----------
def origin() -> str:
    return os.environ.get("MG_PUBLIC_URL", "https://radio.guildawork.com").rstrip("/")


def issuer() -> str:
    return os.environ.get("MG_OIDC_ISSUER", "").rstrip("/") + "/"


def oidc_client_id() -> str:
    return os.environ.get("MG_OIDC_CLIENT_ID", "radio-guilda-web")


def enabled() -> bool:
    return bool(os.environ.get("MG_OIDC_ISSUER"))


def allowed_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("MG_OAUTH_ALLOWED_EMAILS", "").split(",") if e.strip()}


# ---------- utilidades ----------
def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def rnd(n: int) -> str:
    return secrets.token_urlsafe(n)


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not (43 <= len(verifier) <= 128) or not all(c.isalnum() or c in "._~-" for c in verifier):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


def valid_redirect(value) -> bool:
    if not isinstance(value, str) or len(value) > 500:
        return False
    try:
        u = urlparse(value)
    except ValueError:
        return False
    if u.fragment or not u.hostname:
        return False
    if u.scheme == "https":
        return True
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")


def _prune() -> None:
    now = time.time()
    for store in (_pending, _codes):
        for k in [k for k, v in store.items() if v["expires"] < now]:
            store.pop(k, None)


def _back(redirect_uri: str, **params) -> RedirectResponse:
    u = urlparse(redirect_uri)
    q = [(k, v) for k, v in params.items() if v is not None]
    sep = "&" if u.query else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(q), status_code=302, headers={"cache-control": "no-store"})


_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http://localhost:* http://127.0.0.1:*; "
             "frame-ancestors 'none'; base-uri 'none'")


def _page(status: int, title: str, body: str) -> HTMLResponse:
    page = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{html.escape(title)}</title><style>'
            'body{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;min-height:100vh;padding:16px;box-sizing:border-box}'
            'main{max-width:30rem;width:100%}h1{font-size:1.3rem}ul{padding-left:1.2rem}'
            'form{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.2rem}'
            'button{font:inherit;padding:.6rem 1.1rem;border-radius:.5rem;border:1px solid #888;cursor:pointer;background:transparent;color:inherit}'
            'button.ok{background:#0b6bcb;border-color:#0b6bcb;color:#fff}</style></head>'
            f'<body><main>{body}</main></body></html>')
    return HTMLResponse(page, status_code=status, headers={"cache-control": "no-store", "x-frame-options": "DENY",
                                                           "content-security-policy": _PAGE_CSP})


def _fail(message: str) -> HTMLResponse:
    return _page(400, "No se puede autorizar", f"<h1>No se puede autorizar</h1><p>{html.escape(message)}</p>")


def _cors_json(status: int, data: dict) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={**CORS, "cache-control": "no-store"})


def _token_error(status: int, error: str, description: str) -> JSONResponse:
    return _cors_json(status, {"error": error, "error_description": description})


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body())[:100_000]
    if "application/json" in request.headers.get("content-type", ""):
        try:
            return {k: v for k, v in json.loads(raw or b"{}").items() if isinstance(v, str)}
        except ValueError:
            return {}
    return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}


def _get_client(client_id: str | None) -> dict | None:
    with db.pool().connection() as con:
        return con.execute("SELECT client_id, secret_hash, name, redirect_uris FROM oauth_clients WHERE client_id=%s",
                           [(client_id or "")[:100]]).fetchone()


async def hydra_profile(http: httpx.AsyncClient, code: str, verifier: str, redirect_uri: str) -> dict:
    """Canjea el código de Hydra (PKCE) y devuelve el perfil OIDC (sub, email). Lanza ValueError si algo falla."""
    tr = await http.post(issuer() + "oauth2/token", data={
        "grant_type": "authorization_code", "client_id": oidc_client_id(), "code": code,
        "redirect_uri": redirect_uri, "code_verifier": verifier})
    tokens = tr.json()
    if tr.status_code != 200 or not tokens.get("access_token"):
        raise ValueError("token")
    ur = await http.get(issuer() + "userinfo", headers={"authorization": "Bearer " + tokens["access_token"]})
    profile = ur.json()
    if ur.status_code != 200 or not profile.get("sub"):
        raise ValueError("userinfo")
    return profile


def hydra_url(state: str, verifier: str, redirect_uri: str) -> str:
    return issuer() + "oauth2/auth?" + urlencode({
        "client_id": oidc_client_id(), "response_type": "code", "scope": "openid email profile", "state": state,
        "redirect_uri": redirect_uri, "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()})


def consent_page(tx: dict, client_name: str) -> HTMLResponse:
    """Pantalla de consentimiento para la transacción `tx` (ya con sub y email); la deja pendiente y fija la cookie."""
    cid = rnd(24)
    _pending[cid] = {**tx, "step": "consent", "expires": time.time() + TX_SECONDS}
    email = tx["email"]
    body = (f"<h1>¿Dar acceso a «{html.escape(client_name)}»?</h1>"
            f"<p>Esta aplicación quiere usar Meteo Guilda · Radio Guilda como <b>{html.escape(email or 'tu cuenta')}</b>. Podrá:</p>"
            "<ul><li>consultar previsiones, balizas, repetidores, satélites y propagación</li>"
            "<li>planificar rutas con la meteo a la hora de paso</li>"
            "<li>usar tu cuaderno de contactos si le facilitas la clave del cuaderno</li></ul>"
            "<p>Puedes retirarle el acceso cuando quieras.</p>"
            f'<form method="post" action="/api/oauth/consent"><input type="hidden" name="tx" value="{cid}">'
            '<button class="ok" name="decision" value="allow">Permitir</button><button name="decision" value="deny">Cancelar</button></form>')
    r = _page(200, "Autorizar acceso a Radio Guilda", body)
    r.set_cookie(COOKIE, cid, max_age=TX_SECONDS, path="/api/oauth", httponly=True, secure=True, samesite="lax")
    return r


# ---------- metadatos (se sirven también en /.well-known/…) ----------
@router.get("/.well-known/oauth-authorization-server")
@router.get("/api/oauth/metadata")
def metadata():
    o = origin()
    return JSONResponse({
        "issuer": o, "authorization_endpoint": f"{o}/api/oauth/authorize", "token_endpoint": f"{o}/api/oauth/token",
        "registration_endpoint": f"{o}/api/oauth/register", "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": [SCOPE], "authorization_response_iss_parameter_supported": True,
    }, headers={**CORS, "cache-control": "public, max-age=300"})


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    o = origin()
    return JSONResponse({"resource": f"{o}/mcp", "authorization_servers": [o], "bearer_methods_supported": ["header"],
                         "scopes_supported": [SCOPE]}, headers={**CORS, "cache-control": "public, max-age=300"})


@router.options("/api/oauth/{rest:path}")
def preflight(rest: str):
    return JSONResponse(None, status_code=204, headers={**CORS, "access-control-max-age": "600"})


# ---------- registro dinámico de clientes ----------
@router.post("/api/oauth/register")
async def register(request: Request):
    now = time.time()
    while _registrations and _registrations[0] < now - 3600:
        _registrations.pop(0)
    if len(_registrations) >= REGISTERS_PER_HOUR:
        return _cors_json(429, {"error": "temporarily_unavailable", "error_description": "Demasiados registros; inténtalo más tarde."})
    try:
        body = json.loads((await request.body())[:100_000] or b"{}")
    except ValueError:
        return _cors_json(400, {"error": "invalid_client_metadata"})
    redirects = body.get("redirect_uris") if isinstance(body, dict) else None
    if not isinstance(redirects, list) or not redirects or len(redirects) > 10 or not all(valid_redirect(r) for r in redirects):
        return _cors_json(400, {"error": "invalid_redirect_uri",
                                "error_description": "redirect_uris debe contener URLs https (o http en localhost)."})
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return _cors_json(400, {"error": "invalid_client_metadata", "error_description": "Método de autenticación no admitido."})
    name = "".join(c if c >= " " else " " for c in str(body.get("client_name") or "Cliente MCP")).strip()[:80] or "Cliente MCP"
    client_id = "mg_cli_" + rnd(18)
    secret = None if method == "none" else "mg_sec_" + rnd(32)
    with db.pool().connection() as con:
        if con.execute("SELECT count(*) AS n FROM oauth_clients").fetchone()["n"] >= MAX_CLIENTS:
            return _cors_json(503, {"error": "temporarily_unavailable"})
        con.execute("INSERT INTO oauth_clients(client_id, secret_hash, name, redirect_uris) VALUES (%s,%s,%s,%s)",
                    [client_id, sha256(secret) if secret else None, name, json.dumps(redirects)])
    _registrations.append(now)
    out = {"client_id": client_id, "client_id_issued_at": int(now), "client_name": name, "redirect_uris": redirects,
           "token_endpoint_auth_method": method, "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "scope": SCOPE}
    if secret:
        out.update(client_secret=secret, client_secret_expires_at=0)
    return _cors_json(201, out)


# ---------- autorización ----------
@router.get("/api/oauth/authorize")
def authorize(request: Request):
    if not enabled():
        return _fail("El inicio de sesión no está configurado en este servidor.")
    _prune()
    q = request.query_params
    client = _get_client(q.get("client_id"))
    if not client:
        return _fail("La aplicación que te ha enviado aquí no está registrada.")
    redirect_uri = q.get("redirect_uri")
    if not redirect_uri or redirect_uri not in client["redirect_uris"]:
        return _fail("La dirección de retorno no coincide con la registrada.")
    state = q.get("state") or None
    iss = origin()
    if q.get("response_type") != "code":
        return _back(redirect_uri, error="unsupported_response_type", state=state, iss=iss)
    challenge = q.get("code_challenge") or ""
    if q.get("code_challenge_method") != "S256" or not (43 <= len(challenge) <= 128):
        return _back(redirect_uri, error="invalid_request", error_description="Se exige PKCE con S256.", state=state, iss=iss)
    from . import auth  # import tardío: auth importa este módulo
    user = auth.session_user(request)
    base = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "state": state, "challenge": challenge}
    if user and (not allowed_emails() or user["email"] in allowed_emails()):
        # ya hay sesión en la web: no hace falta volver a Hydra
        return consent_page({**base, "sub": user["sub"], "email": user["email"]}, client["name"])
    tx = rnd(24)
    verifier = rnd(48)
    _pending[tx] = {**base, "step": "login", "verifier": verifier, "expires": time.time() + TX_SECONDS}
    r = RedirectResponse(hydra_url(tx, verifier, origin() + "/api/oauth/callback"), status_code=302, headers={"cache-control": "no-store"})
    r.set_cookie(COOKIE, tx, max_age=TX_SECONDS, path="/api/oauth", httponly=True, secure=True, samesite="lax")
    return r


@router.get("/api/oauth/callback")
async def callback(request: Request):
    _prune()
    q = request.query_params
    state = q.get("state", "")
    tx = _pending.pop(state, None)
    if not tx or tx["step"] != "login" or not hmac.compare_digest(request.cookies.get(COOKIE, ""), state) or not q.get("code"):
        return _fail("La solicitud ha caducado o no es tuya. Vuelve a empezar desde la aplicación.")
    try:
        profile = await hydra_profile(request.app.state.http, q["code"], tx["verifier"], origin() + "/api/oauth/callback")
    except Exception as e:  # noqa: BLE001
        log.warning("oauth callback: %s", e)
        return _fail("No se ha podido comprobar tu inicio de sesión. Vuelve a intentarlo.")
    email = (profile.get("email") or "").strip().lower()
    allowed = allowed_emails()
    if allowed and email not in allowed:
        log.warning("oauth: acceso denegado a %s", email)
        return _back(tx["redirect_uri"], error="access_denied", error_description="Tu cuenta no tiene acceso.", state=tx["state"], iss=origin())
    with db.pool().connection() as con:
        client = con.execute("SELECT name FROM oauth_clients WHERE client_id=%s", [tx["client_id"]]).fetchone()
    return consent_page({**tx, "sub": profile["sub"], "email": email}, client["name"] if client else "una aplicación")


@router.post("/api/oauth/consent")
async def consent(request: Request):
    _prune()
    form = await _form(request)
    cid = form.get("tx", "")
    tx = _pending.pop(cid, None)
    if not tx or tx["step"] != "consent" or not hmac.compare_digest(request.cookies.get(COOKIE, ""), cid):
        return _fail("La solicitud ha caducado o no es tuya. Vuelve a empezar desde la aplicación.")
    if form.get("decision") != "allow":
        return _back(tx["redirect_uri"], error="access_denied", state=tx["state"], iss=origin())
    code = rnd(32)
    _codes[sha256(code)] = {"sub": tx["sub"], "email": tx["email"], "client_id": tx["client_id"], "redirect_uri": tx["redirect_uri"],
                            "challenge": tx["challenge"], "expires": time.time() + CODE_SECONDS}
    r = _back(tx["redirect_uri"], code=code, state=tx["state"], iss=origin())
    r.delete_cookie(COOKIE, path="/api/oauth")
    return r


# ---------- tokens ----------
def _issue(sub: str, email: str, client_id: str, row_id: int | None = None) -> dict:
    access, refresh = "mg_at_" + rnd(32), "mg_rt_" + rnd(32)
    with db.pool().connection() as con:
        if row_id:
            n = con.execute("UPDATE oauth_tokens SET access_hash=%s, refresh_hash=%s, expires_at=now()+make_interval(secs=>%s), "
                            "refresh_expires_at=now()+make_interval(days=>%s) WHERE id=%s AND revoked_at IS NULL",
                            [sha256(access), sha256(refresh), ACCESS_SECONDS, REFRESH_DAYS, row_id]).rowcount
            if not n:
                return {}
        else:
            con.execute("INSERT INTO oauth_tokens(sub, email, client_id, access_hash, expires_at, refresh_hash, refresh_expires_at) "
                        "VALUES (%s,%s,%s,%s,now()+make_interval(secs=>%s),%s,now()+make_interval(days=>%s))",
                        [sub, email, client_id, sha256(access), ACCESS_SECONDS, sha256(refresh), REFRESH_DAYS])
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS, "refresh_token": refresh, "scope": SCOPE}


@router.post("/api/oauth/token")
async def token(request: Request):
    _prune()
    form = await _form(request)
    client_id, secret = form.get("client_id"), form.get("client_secret")
    basic = request.headers.get("authorization", "")
    if basic.startswith("Basic "):
        try:
            cid, _, sec = base64.b64decode(basic[6:]).decode().partition(":")
            client_id, secret = unquote(cid), unquote(sec)
        except Exception:  # noqa: BLE001
            return _token_error(401, "invalid_client", "Credenciales mal formadas.")
    client = _get_client(client_id)
    if not client:
        return _token_error(401, "invalid_client", "Cliente desconocido.")
    if client["secret_hash"] and not (secret and hmac.compare_digest(sha256(secret), client["secret_hash"])):
        return _token_error(401, "invalid_client", "Secreto incorrecto.")
    grant = form.get("grant_type")
    if grant == "authorization_code":
        data = _codes.pop(sha256(form.get("code", "")), None)
        if (not data or data["expires"] < time.time() or data["client_id"] != client["client_id"]
                or data["redirect_uri"] != form.get("redirect_uri")):
            return _token_error(400, "invalid_grant", "Código no válido o caducado.")
        if not pkce_ok(form.get("code_verifier", ""), data["challenge"]):
            return _token_error(400, "invalid_grant", "La verificación PKCE no coincide.")
        return _cors_json(200, _issue(data["sub"], data["email"], client["client_id"]))
    if grant == "refresh_token":
        with db.pool().connection() as con:
            row = con.execute("SELECT id, sub, email FROM oauth_tokens WHERE refresh_hash=%s AND client_id=%s AND revoked_at IS NULL "
                              "AND refresh_expires_at>now()", [sha256(form.get("refresh_token", "")), client["client_id"]]).fetchone()
        issued = _issue(row["sub"], row["email"], client["client_id"], row["id"]) if row else {}
        if not issued:
            return _token_error(400, "invalid_grant", "Token de renovación no válido o caducado.")
        return _cors_json(200, issued)
    return _token_error(400, "unsupported_grant_type", "Solo authorization_code y refresh_token.")


@router.post("/api/oauth/introspect")
async def introspect(request: Request, x_admin_token: str | None = Header(default=None)):
    """Lo usa el servicio MCP para validar el token de acceso. Solo con el token de administración."""
    expected = os.environ.get("MG_ADMIN_TOKEN")
    if not expected or not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "No autorizado")
    tok = (await _form(request)).get("token", "")
    with db.pool().connection() as con:
        row = con.execute("SELECT email, client_id, expires_at FROM oauth_tokens WHERE access_hash=%s AND revoked_at IS NULL "
                          "AND expires_at>now()", [sha256(tok)]).fetchone() if tok else None
    if not row:
        return {"active": False}
    return {"active": True, "email": row["email"], "client_id": row["client_id"], "exp": int(row["expires_at"].timestamp())}
