"""Ajustes de la cuenta: IA interna (activar/desactivar), proveedor y modelo, y claves propias de la persona (cifradas en reposo).

La identidad es la cuenta de Guilda Work (sesión de auth.py). Las claves de API se guardan cifradas con Fernet (MG_SECRET_KEY) y NUNCA
se devuelven: la API solo dice si hay clave y sus 4 últimos caracteres."""
from __future__ import annotations

import hmac
import json
import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import ai, auth, db

log = logging.getLogger("meteoguilda.account")
router = APIRouter()
DEFAULTS = {"ai_enabled": True, "provider": "server", "model": "", "home": None}


def _fernet():
    key = os.environ.get("MG_SECRET_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def current(request: Request) -> dict:
    """Usuario de la sesión (o, solo en pruebas con MG_QA, uno ficticio). 401 si no hay."""
    u = auth.session_user(request)
    if u:
        return u
    # El servicio MCP actúa en nombre de la persona que se autenticó con OAuth: token de administración + identidad (solo red interna de Docker)
    tok, sub, exp = request.headers.get("x-admin-token"), request.headers.get("x-act-as"), os.environ.get("MG_ADMIN_TOKEN")
    if sub and tok and exp and hmac.compare_digest(tok, exp):
        return {"sub": sub[:200], "email": request.headers.get("x-act-email", "")[:200]}
    if os.environ.get("MG_QA"):
        return {"sub": "qa", "email": "qa@local"}
    raise HTTPException(401, "Inicia sesión", headers={"x-auth": "login"})


def load(sub: str) -> tuple[dict, dict]:
    """(ajustes, claves cifradas) de una persona."""
    with db.pool().connection() as con:
        row = con.execute("SELECT settings, keys FROM user_settings WHERE sub=%s", [sub]).fetchone()
    return ({**DEFAULTS, **(row["settings"] if row else {})}, dict(row["keys"]) if row else {})


def _save(sub: str, settings: dict | None = None, keys: dict | None = None) -> None:
    cur_s, cur_k = load(sub)
    with db.pool().connection() as con:
        con.execute("INSERT INTO user_settings(sub, settings, keys) VALUES (%s,%s,%s) ON CONFLICT (sub) DO UPDATE "
                    "SET settings=EXCLUDED.settings, keys=EXCLUDED.keys, updated_at=now()",
                    [sub, json.dumps(settings if settings is not None else cur_s), json.dumps(keys if keys is not None else cur_k)])


def decrypt(token: str) -> str | None:
    f = _fernet()
    if not f:
        return None
    try:
        return f.decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001
        return None


def config_for(request: Request) -> tuple[dict, ai.AIConfig]:
    """Ajustes y configuración de IA efectiva de quien hace la petición (por defecto, la del servidor)."""
    user = current(request)
    s, keys = load(user["sub"])
    prov = s["provider"] if s["provider"] in ai.PROVIDERS else "server"
    key = decrypt(keys[prov]) if prov != "server" and prov in keys else None
    if prov != "server" and not key:
        prov = "server"          # sin clave propia utilizable: se vuelve al servidor
    if prov == "server" and (m := ai.server_mistral(s.get("model"))):
        return s, m              # el servidor tiene clave de Mistral: su IA es Mistral, con el modelo que elija la persona (los gratuitos de OpenRouter no se usan)
    return s, ai.AIConfig(provider=prov, model=s.get("model", "") if prov != "server" or s.get("model") else "", api_key=key)


class HomeIn(BaseModel):
    """«Mi ubicación» (lat/lon redondeadas a ~100 m): la usan el asistente y el MCP para responder sobre dónde está la persona."""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = Field(None, max_length=80)


class SettingsIn(BaseModel):
    home: HomeIn | None = None
    ai_enabled: bool | None = None
    provider: str | None = None
    model: str | None = Field(None, max_length=120)


@router.get("/api/me/settings")
def get_settings(request: Request):
    user = current(request)
    s, keys = load(user["sub"])
    info = {}
    for p, tok in keys.items():
        k = decrypt(tok)
        info[p] = {"set": bool(k), "last4": k[-4:] if k else None}
    return {"email": user["email"], **s, "keys": info, "can_store_keys": _fernet() is not None,
            "server_ai": ai.server_ai(), "server_mistral": bool(ai.server_mistral()), "server_model": ai.server_mistral_model() if ai.server_mistral() else None,
            "providers": {k: {"label": ai.server_label() if k == "server" else v["label"], "key": v["key"],
                              "suggested": ai.SUGGESTED["mistral"] if k == "server" and ai.server_mistral() else ai.SUGGESTED.get(k, [])} for k, v in ai.PROVIDERS.items()}}


@router.put("/api/me/settings")
def put_settings(request: Request, body: SettingsIn):
    user = current(request)
    s, _ = load(user["sub"])
    if body.provider is not None:
        if body.provider not in ai.PROVIDERS:
            raise HTTPException(422, "Proveedor desconocido")
        s["provider"] = body.provider
    if "home" in body.model_fields_set:   # null borra la ubicación
        s["home"] = {"lat": round(body.home.lat, 3), "lon": round(body.home.lon, 3), "name": (body.home.name or "").strip() or None} if body.home else None
    if body.ai_enabled is not None:
        s["ai_enabled"] = body.ai_enabled
    if body.model is not None:
        s["model"] = body.model.strip()
    _save(user["sub"], settings=s)
    return get_settings(request)


class KeyIn(BaseModel):
    provider: str
    key: str = Field(min_length=8, max_length=400)


@router.put("/api/me/key")
def put_key(request: Request, body: KeyIn):
    user = current(request)
    if body.provider not in ai.PROVIDERS or not ai.PROVIDERS[body.provider]["key"]:
        raise HTTPException(422, "Ese proveedor no usa clave propia")
    f = _fernet()
    if not f:
        raise HTTPException(503, "El servidor no puede guardar claves (falta MG_SECRET_KEY)")
    k = body.key.strip()
    if any(c.isspace() or c in "\"'" for c in k):
        raise HTTPException(422, "La clave contiene espacios o comillas")
    _, keys = load(user["sub"])
    keys[body.provider] = f.encrypt(k.encode()).decode()
    _save(user["sub"], keys=keys)
    return get_settings(request)


@router.delete("/api/me/key")
def delete_key(request: Request, provider: str):
    user = current(request)
    _, keys = load(user["sub"])
    keys.pop(provider, None)
    _save(user["sub"], keys=keys)
    return get_settings(request)


@router.get("/api/me/models")
async def models(request: Request, provider: str):
    user = current(request)
    if provider not in ai.PROVIDERS:
        raise HTTPException(422, "Proveedor desconocido")
    if provider == "server" and (m := ai.server_mistral()):     # IA del servidor en Mistral: sus modelos, con la clave del servidor (que no sale de aquí)
        return {"provider": provider, "models": await ai.list_models(request.app.state.http, m)}
    _, keys = load(user["sub"])
    key = decrypt(keys[provider]) if provider in keys else None
    return {"provider": provider, "models": await ai.list_models(request.app.state.http, ai.AIConfig(provider, "", key))}


@router.get("/api/me/ai-status")
def ai_status(request: Request):
    """Estado de los modelos gratuitos del servidor (cuál responde, cuál está en pausa por límite de uso y cuánto falta)."""
    current(request)
    return {"modelos": ai.estado_modelos()[:8], "nota": "Si un modelo responde 429 o vacío, se pausa unos minutos y se prueba el siguiente."}


@router.post("/api/me/test")
async def test(request: Request):
    """Prueba la configuración de IA actual con una petición mínima."""
    _, cfg = config_for(request)
    t0 = time.time()
    try:
        if cfg.provider == "server":
            if not os.environ.get("OPENROUTER_API_KEY"):
                raise ValueError("El servidor no tiene clave de IA configurada")
            last = None
            for m in await ai.ranked_free_models(request.app.state.http, 6):
                try:
                    txt = await ai.complete(request.app.state.http, cfg, "Responde solo OK.", [{"role": "user", "content": "Di OK"}], model=m, max_tokens=20, timeout=40)
                    ai.note(m, None)
                    return {"ok": True, "provider": "server", "model": m, "ms": round((time.time() - t0) * 1000), "respuesta": txt.strip()[:40]}
                except (httpx.HTTPError, ValueError) as e:
                    last = e
                    ai.note(m, ai.kind_of(e))
            raise ValueError(f"Ningún modelo gratuito responde ahora ({ai.friendly_error(last)})")
        txt = await ai.complete(request.app.state.http, cfg, "Responde solo OK.", [{"role": "user", "content": "Di OK"}], max_tokens=20, timeout=40)
        return {"ok": True, "provider": cfg.provider, "model": cfg.model, "ms": round((time.time() - t0) * 1000), "respuesta": txt.strip()[:40]}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": ai.friendly_error(e)}


@router.delete("/api/me", status_code=204)
def delete_me(request: Request):
    """Borra los ajustes, claves, memoria del asistente, dispositivos de notificaciones, alertas y rutas guardadas de la persona en este servicio."""
    user = current(request)
    from . import alertas, push, rutas_guardadas
    push.borrar_de(user["sub"])          # dispositivos de notificaciones
    alertas.borrar_de(user["sub"])       # alertas de radio y salidas, sus límites y lo enviado
    rutas_guardadas.borrar_de(user["sub"])
    with db.pool().connection() as con:
        con.execute("DELETE FROM user_settings WHERE sub=%s", [user["sub"]])   # ajustes, claves y memoria del asistente
