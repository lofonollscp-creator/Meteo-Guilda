"""Vigilante: convierte el estado del sistema en alertas y, si se configura, avisa por push a quien administra.

Existe porque un fallo silencioso (APRS sin recibir nada) pasó 3 días sin que nadie se enterase. Solo agregados y nombres de variables, nunca datos
de personas ni valores de configuración. Aviso push: MG_ADMIN_SUBS = identificadores (`sub`) de las cuentas que lo reciben, separados por comas."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from . import configuracion, db, push

SILENCIO_APRS_H = float(os.environ.get("MG_APRS_SILENCIO_H", "3"))


def alertas(est: dict, config: list[str] | None = None) -> list[dict]:
    """Alertas activas a partir del dict de `estado.estado()`. Cada una: {id, nivel, texto}."""
    out = []
    edad = (est.get("aprs") or {}).get("ultimo_paquete_hace_s")
    if edad is None:
        out.append({"id": "aprs", "nivel": "alto", "texto": "APRS: nunca ha entrado ningún paquete de estaciones."})
    elif edad > SILENCIO_APRS_H * 3600:
        out.append({"id": "aprs", "nivel": "alto", "texto": f"APRS: ningún paquete de estaciones desde hace {int(edad // 3600)} h."})
    for i, t in enumerate(configuracion.avisos() if config is None else config):
        out.append({"id": f"config{i}", "nivel": "medio", "texto": "Configuración: " + t})
    return out


def _cuentas() -> list[str]:
    return [s.strip() for s in os.environ.get("MG_ADMIN_SUBS", "").split(",") if s.strip()]


def avisar_admin(lista: list[dict], ahora: datetime | None = None) -> int:
    """Encola un push por alerta de nivel alto a los dispositivos de MG_ADMIN_SUBS. Como mucho uno cada 12 h por alerta (clave única)."""
    cuentas, ahora = _cuentas(), ahora or datetime.now(timezone.utc)
    altas = [a for a in lista if a["nivel"] == "alto"]
    if not cuentas or not altas:
        return 0
    n = 0
    with db.pool().connection() as con:
        eps = [r["endpoint"] for r in con.execute("SELECT endpoint FROM push_subs WHERE sub = ANY(%s)", [cuentas]).fetchall()]
        for a in altas:
            clave = f"vigilante:{a['id']}:{ahora:%Y-%m-%d}:{ahora.hour // 12}"
            for ep in eps:
                n += push.encolar(con, ep, "Meteo Guilda: algo falla", a["texto"], "/", clave=clave)
    return n


def revisar() -> int:
    """Una pasada completa (síncrona): estado → alertas → log y push. Devuelve cuántos push se encolaron."""
    from . import estado
    lista = alertas(estado.estado())
    return avisar_admin(lista)
