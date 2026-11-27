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
    if (est.get("claves") or {}).get("aemet"):
        av = est.get("avisos_aemet") or {}
        if av.get("espera_reintento_s") and (av.get("edad_cache_s") is None or av["edad_cache_s"] > 6 * 3600):
            out.append({"id": "aemet", "nivel": "medio", "texto": "AEMET: los avisos oficiales llevan más de 6 h sin actualizarse (la fuente está dando error o limitando)."})
    mc = est.get("meteocat") or {}
    if mc.get("tope_mes") and mc.get("consultas_mes", 0) >= 0.8 * mc["tope_mes"]:
        out.append({"id": "meteocat", "nivel": "medio", "texto": f"Meteocat: {mc['consultas_mes']} de {mc['tope_mes']} consultas del mes gastadas; al llegar al tope se sirve lo último guardado."})
    ia = est.get("asistente") or {}
    if ia.get("tope_por_persona") and (ia.get("maximo_por_persona") or 0) >= ia["tope_por_persona"]:
        out.append({"id": "ia_tope", "nivel": "bajo", "texto": f"Asistente: alguien ha llegado hoy al tope de {ia['tope_por_persona']} consultas con la IA del servidor."})
    ver = est.get("verificacion") or {}
    if ver.get("registros") and (ver.get("ultimo_registro_hace_s") or 0) > 48 * 3600:
        out.append({"id": "verificacion", "nivel": "medio", "texto": "Verificación: no se guardan previsiones nuevas desde hace más de 48 h (el pregenerador puede estar parado)."})
    for i, t in enumerate(configuracion.avisos() if config is None else config):
        out.append({"id": f"config{i}", "nivel": "medio", "texto": "Configuración: " + t})
    return out


def semaforo(lista: list[dict]) -> str:
    return "rojo" if any(a["nivel"] == "alto" for a in lista) else "amarillo" if any(a["nivel"] == "medio" for a in lista) else "verde"   # «bajo» solo informa


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
