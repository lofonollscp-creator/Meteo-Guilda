import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from meteoguilda import mcp_server, oauth


def _challenge(v: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_pkce_y_redirecciones():
    v = "a" * 50
    assert oauth.pkce_ok(v, _challenge(v))
    assert not oauth.pkce_ok(v, _challenge("b" * 50))
    assert not oauth.pkce_ok("corto", _challenge("corto"))
    assert oauth.valid_redirect("https://claude.ai/api/mcp/auth_callback")
    assert oauth.valid_redirect("http://localhost:33418/callback")
    assert not oauth.valid_redirect("http://evil.example/cb")
    assert not oauth.valid_redirect("https://x.example/cb#frag")
    assert not oauth.valid_redirect("javascript:alert(1)")


def test_bearer_acepta_clave_fija_y_token_oauth_y_anuncia_el_oauth():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    llamadas = []
    def intro(t):
        llamadas.append(t)
        return t == "mg_at_bueno"
    c = TestClient(mcp_server._Bearer(inner, "fija", intro, "https://x/.well-known/oauth-protected-resource/mcp"))
    r = c.get("/")
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
    assert c.get("/", headers={"Authorization": "Bearer nada"}).status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer fija"}).status_code == 200
    assert c.get("/", headers={"Authorization": "Bearer mg_at_bueno"}).status_code == 200
    assert c.get("/", headers={"Authorization": "Bearer mg_at_bueno"}).status_code == 200
    assert llamadas.count("mg_at_bueno") == 1   # la segunda sale de la caché


def test_sin_configuracion_oauth_ni_clave_el_mcp_no_arranca(monkeypatch):
    for v in ("MG_MCP_TOKEN", "MG_OIDC_ISSUER"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(SystemExit):
        mcp_server.http_app()


@pytest.fixture
def cliente(pg, monkeypatch):
    from meteoguilda import db, main
    monkeypatch.setenv("MG_OIDC_ISSUER", "https://hydra.test")
    monkeypatch.setenv("MG_PUBLIC_URL", "https://radio.test")
    monkeypatch.setenv("MG_ADMIN_TOKEN", "admin-secreto")
    monkeypatch.delenv("MG_OAUTH_ALLOWED_EMAILS", raising=False)
    with db.pool().connection() as con:
        con.execute("TRUNCATE oauth_tokens, oauth_clients, web_sessions RESTART IDENTITY CASCADE")
    from meteoguilda import auth
    auth._cache.clear(); oauth._pending.clear(); oauth._codes.clear(); oauth._registrations.clear()

    def hydra(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "hydra-at"})
        if request.url.path.endswith("/userinfo"):
            return httpx.Response(200, json={"sub": "u-1", "email": "Ana@Guilda.test"})
        return httpx.Response(404)

    with TestClient(main.app, base_url="https://radio.test") as c:
        c.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(hydra))
        yield c


def _registrar(c, **extra):
    r = c.post("/api/oauth/register", json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/cb"], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _autorizar(c, cli, verifier):
    r = c.get("/api/oauth/authorize", params={"client_id": cli["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code",
                                              "code_challenge": _challenge(verifier), "code_challenge_method": "S256", "state": "xyz"},
              follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://hydra.test/oauth2/auth?")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == ["radio-guilda-web"] and q["code_challenge_method"] == ["S256"]
    r = c.get("/api/oauth/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 200 and "Ana@Guilda.test".lower() in r.text
    tx = re.search(r'name="tx" value="([^"]+)"', r.text).group(1)
    r = c.post("/api/oauth/consent", data={"tx": tx, "decision": "allow"}, follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "claude.ai"
    q = parse_qs(loc.query)
    assert q["state"] == ["xyz"] and q["iss"] == ["https://radio.test"]
    return q["code"][0]


def test_flujo_completo_con_renovacion_e_introspeccion(cliente):
    c = cliente
    assert c.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == "https://radio.test/mcp"
    assert c.get("/.well-known/oauth-authorization-server").json()["code_challenge_methods_supported"] == ["S256"]
    cli = _registrar(c)
    verifier = "v" * 60
    code = _autorizar(c, cli, verifier)
    datos = {"grant_type": "authorization_code", "client_id": cli["client_id"], "code": code, "redirect_uri": "https://claude.ai/cb"}
    assert c.post("/api/oauth/token", data={**datos, "code_verifier": "x" * 60}).status_code == 400   # PKCE malo (y el código se quema)
    code = _autorizar(c, cli, verifier)
    r = c.post("/api/oauth/token", data={**datos, "code": code, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["access_token"].startswith("mg_at_") and tok["expires_in"] == 3600
    assert c.post("/api/oauth/token", data={**datos, "code": code, "code_verifier": verifier}).status_code == 400   # un solo uso
    adm = {"X-Admin-Token": "admin-secreto"}
    assert c.post("/api/oauth/introspect", data={"token": tok["access_token"]}).status_code == 403
    i = c.post("/api/oauth/introspect", data={"token": tok["access_token"]}, headers=adm).json()
    assert i["active"] and i["email"] == "ana@guilda.test"
    assert not c.post("/api/oauth/introspect", data={"token": "mg_at_falso"}, headers=adm).json()["active"]
    r = c.post("/api/oauth/token", data={"grant_type": "refresh_token", "client_id": cli["client_id"], "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200
    nuevo = r.json()
    assert not c.post("/api/oauth/introspect", data={"token": tok["access_token"]}, headers=adm).json()["active"]   # rotación
    assert c.post("/api/oauth/introspect", data={"token": nuevo["access_token"]}, headers=adm).json()["active"]
    assert c.post("/api/oauth/token", data={"grant_type": "refresh_token", "client_id": cli["client_id"],
                                            "refresh_token": tok["refresh_token"]}).status_code == 400


def test_rechazos(cliente, monkeypatch):
    c = cliente
    cli = _registrar(c)
    base = {"client_id": cli["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code", "state": "s"}
    # sin PKCE: vuelve al cliente con error
    r = c.get("/api/oauth/authorize", params=base, follow_redirects=False)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
    # redirect no registrado: página de error, sin redirigir
    r = c.get("/api/oauth/authorize", params={**base, "redirect_uri": "https://evil.test/cb"}, follow_redirects=False)
    assert r.status_code == 400
    # cliente desconocido
    assert c.get("/api/oauth/authorize", params={**base, "client_id": "nope"}, follow_redirects=False).status_code == 400
    # registro con redirect peligroso
    assert c.post("/api/oauth/register", json={"redirect_uris": ["http://evil.test/cb"]}).status_code == 400
    # callback sin la cookie de la transacción
    r = c.get("/api/oauth/authorize", params={**base, "code_challenge": _challenge("v" * 60), "code_challenge_method": "S256"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    c.cookies.clear()
    assert c.get("/api/oauth/callback", params={"code": "c", "state": state}).status_code == 400
    # lista de correos permitidos
    monkeypatch.setenv("MG_OAUTH_ALLOWED_EMAILS", "otra@guilda.test")
    r = c.get("/api/oauth/authorize", params={**base, "code_challenge": _challenge("v" * 60), "code_challenge_method": "S256"}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = c.get("/api/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and "error=access_denied" in r.headers["location"]


# ---------- puerta de acceso de la web (auth.py) ----------
def test_safe_return():
    from meteoguilda import auth
    assert auth.safe_return("/radio?x=1") == "/radio?x=1"
    for malo in (None, "", "https://evil.test/", "//evil.test", "/\\evil", "javascript:alert(1)", "/a\nb"):
        assert auth.safe_return(malo) == "/"


def _entrar(c, volver="/radio"):
    r = c.get("/api/auth/login", params={"return_to": volver}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://hydra.test/oauth2/auth?")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://radio.test/api/auth/callback"]
    return c.get("/api/auth/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)


def test_puerta_de_acceso(cliente):
    c = cliente
    nav = {"sec-fetch-mode": "navigate", "accept": "text/html", "x-forwarded-uri": "/mapa?z=3"}
    r = c.get("/api/auth/check", headers=nav, follow_redirects=False)       # navegador sin sesión → login
    assert r.status_code == 302 and r.headers["location"] == "/api/auth/login?return_to=%2Fmapa%3Fz%3D3"
    r = c.get("/api/auth/check", headers={"accept": "*/*", "sec-fetch-mode": "cors"})   # fetch de la app → 401 marcado
    assert r.status_code == 401 and r.headers["x-auth"] == "login"
    assert c.get("/api/auth/me").status_code == 401
    r = _entrar(c)                                                            # login correcto → cookie de sesión y vuelta a la página
    assert r.status_code == 302 and r.headers["location"] == "/radio"
    assert "mg_session=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    ok = c.get("/api/auth/check", headers=nav)
    assert ok.status_code == 200 and ok.headers["x-user-email"] == "ana@guilda.test"
    assert c.get("/api/auth/me").json() == {"email": "ana@guilda.test"}
    c.get("/api/auth/logout")                                                 # cerrar sesión
    assert c.get("/api/auth/check", headers=nav, follow_redirects=False).status_code == 302
    assert c.get("/api/auth/me").status_code == 401


def test_la_puerta_respeta_la_lista_de_correos_y_la_caducidad(cliente, monkeypatch):
    from meteoguilda import db
    c = cliente
    monkeypatch.setenv("MG_OAUTH_ALLOWED_EMAILS", "otra@guilda.test")
    r = _entrar(c)
    assert r.status_code == 403 and "mg_session" not in r.headers.get("set-cookie", "")
    monkeypatch.delenv("MG_OAUTH_ALLOWED_EMAILS")
    _entrar(c)
    assert c.get("/api/auth/me").status_code == 200
    from meteoguilda import auth
    auth._cache.clear()
    with db.pool().connection() as con:
        con.execute("UPDATE web_sessions SET expires_at = now() - interval '1 minute'")
    assert c.get("/api/auth/me").status_code == 401


def test_callback_de_login_exige_su_cookie_y_un_solo_uso(cliente):
    c = cliente
    r = c.get("/api/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    c.cookies.clear()
    assert c.get("/api/auth/callback", params={"code": "c", "state": state}).status_code == 400   # sin cookie
    assert c.get("/api/auth/callback", params={"code": "c", "state": state}).status_code == 400   # y la transacción ya se quemó


def test_el_mcp_reutiliza_la_sesion_web_sin_volver_a_hydra(cliente):
    c = cliente
    cli = _registrar(c)
    _entrar(c)
    r = c.get("/api/oauth/authorize", params={"client_id": cli["client_id"], "redirect_uri": "https://claude.ai/cb", "response_type": "code",
                                              "code_challenge": _challenge("v" * 60), "code_challenge_method": "S256", "state": "s"},
              follow_redirects=False)
    assert r.status_code == 200 and "ana@guilda.test" in r.text and 'name="tx"' in r.text   # consentimiento directo, sin pasar por Hydra
