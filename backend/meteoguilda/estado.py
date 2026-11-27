"""Panel de salud para quien administra (solo con MG_ADMIN_TOKEN): estado de las fuentes y de los procesos de fondo en un vistazo.
Solo agregados (recuentos, edades, cuotas); nunca datos de personas ni claves."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from . import avisos, meteocat
from .db import pool


def _uno(sql: str, *args) -> dict:
    try:
        with pool().connection() as con:
            return con.execute(sql, list(args)).fetchone() or {}
    except Exception:  # noqa: BLE001
        return {}


def _edad(ts) -> int | None:
    return None if ts is None else max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))


def estado() -> dict:
    ahora = datetime.now(timezone.utc)
    mes, dia = meteocat._consumo(ahora)
    obs = _uno("SELECT max(ts) AS ult, count(*) FILTER (WHERE ts > now() - interval '1 hour') AS h1 FROM obs")
    ver = _uno("SELECT count(*) AS total, count(obs_c) AS verificadas, count(DISTINCT modelo) AS modelos, max(emitida) AS ult FROM verif_pred")
    push = _uno("SELECT (SELECT count(*) FROM push_subs) AS dispositivos, (SELECT count(*) FROM push_cola WHERE enviada IS NULL) AS pendientes, "
                "(SELECT max(enviada) FROM push_cola) AS ult_envio")
    return {
        "hora": ahora.strftime("%Y-%m-%dT%H:%MZ"),
        "claves": {k: bool(os.environ.get(v)) for k, v in {"aemet": "AEMET_API_KEY", "meteocat": "METEOCAT_API_KEY", "vapid": "MG_VAPID_PRIVATE_KEY"}.items()},
        "ponderar_media": os.environ.get("MG_PONDERAR") == "1",
        "aprs": {"ultimo_paquete_hace_s": _edad(obs.get("ult")), "paquetes_ultima_hora": obs.get("h1")},
        "avisos_aemet": {"edad_cache_s": None if not avisos._cache[0] else int(time.time() - avisos._cache[0]), "avisos_vigentes": len(avisos._cache[1]),
                         "espera_reintento_s": max(0, int(avisos._hasta - time.time()))},
        "meteocat": {"consultas_mes": mes, "tope_mes": meteocat.MAX_MES, "consultas_hoy": dia, "tope_dia": meteocat.MAX_DIA},
        "verificacion": {"registros": ver.get("total"), "comparados": ver.get("verificadas"), "modelos": ver.get("modelos"), "ultimo_registro_hace_s": _edad(ver.get("ult"))},
        "push": {"dispositivos": push.get("dispositivos"), "pendientes": push.get("pendientes"), "ultimo_envio_hace_s": _edad(push.get("ult_envio"))},
    }
