"""Tope diario de uso del asistente con la IA DEL SERVIDOR (la que paga la plataforma) y contador para el panel de estado.

- Quien usa su propia clave de IA no tiene tope (no cuesta nada a la plataforma).
- Contadores en `kv_cache`: por persona se guarda un hash corto del identificador (nunca el identificador) y solo del día en curso; los de días anteriores se borran.
- Tope: MG_ASISTENTE_DIA consultas al día por persona (100 por defecto; 0 = sin tope). Se cuenta cada mensaje enviado al asistente."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from fastapi import HTTPException

from .db import pool


def tope() -> int:
    try:
        return max(0, int(os.environ.get("MG_ASISTENTE_DIA", "100")))
    except ValueError:
        return 100


def _dia(ahora: datetime | None = None) -> str:
    return (ahora or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def es_del_servidor(api_key: str | None) -> bool:
    """¿Paga la plataforma? Sí si no hay clave propia (OpenRouter del servidor) o es la clave Mistral del servidor."""
    return api_key is None or api_key == (os.environ.get("MISTRAL_API_KEY") or "").strip()


def _incrementa(con, k: str) -> int:
    r = con.execute("INSERT INTO kv_cache (k, v) VALUES (%s, '1') ON CONFLICT (k) DO UPDATE SET v = (kv_cache.v::int + 1)::text, updated_at = now() RETURNING v", [k]).fetchone()
    return int(r["v"])


def anota(sub: str, ahora: datetime | None = None) -> int:
    """Cuenta una consulta de la persona y del total del día; devuelve cuántas lleva hoy. Nunca lanza: sin BD no hay tope."""
    d = _dia(ahora)
    try:
        h = hashlib.sha256(f"ia|{d}|{sub}".encode()).hexdigest()[:12]
        with pool().connection() as con:
            con.execute("DELETE FROM kv_cache WHERE k LIKE 'ia:%%' AND k NOT LIKE %s", [f"ia:%:{d}%"])
            n = _incrementa(con, f"ia:dia:{d}:{h}")
            _incrementa(con, f"ia:total:{d}")
        return n
    except Exception:  # noqa: BLE001
        return 0


def comprobar(sub: str, api_key: str | None, ahora: datetime | None = None) -> None:
    """Lanza 429 con mensaje claro si la persona ha agotado su tope diario con la IA del servidor."""
    if not es_del_servidor(api_key):
        return
    t = tope()
    n = anota(sub, ahora)
    if t and n > t:
        raise HTTPException(429, f"Has llegado al límite de {t} consultas al día con la IA del servidor. Vuelve mañana o añade tu propia clave de IA en Ajustes.")


def resumen(ahora: datetime | None = None) -> dict:
    """Para el panel: consultas de hoy, personas distintas y el tope (agregados; sin identificadores)."""
    d = _dia(ahora)
    try:
        with pool().connection() as con:
            tot = con.execute("SELECT v FROM kv_cache WHERE k = %s", [f"ia:total:{d}"]).fetchone()
            pers = con.execute("SELECT count(*) AS n, coalesce(max(v::int), 0) AS mx FROM kv_cache WHERE k LIKE %s", [f"ia:dia:{d}:%"]).fetchone()
        return {"consultas_hoy": int(tot["v"]) if tot else 0, "personas_hoy": pers["n"], "maximo_por_persona": pers["mx"], "tope_por_persona": tope()}
    except Exception:  # noqa: BLE001
        return {"consultas_hoy": None, "personas_hoy": None, "maximo_por_persona": None, "tope_por_persona": tope()}
