"""Comprobación de la configuración al arrancar: avisa de valores que casi seguro son un error, sin mostrar nunca el valor (puede ser un secreto).

Caso real: `MG_APRS_FILTER=   # comentario` en el `.env`. Docker (`env_file`) deja el comentario dentro del valor, el filtro APRS-IS llegaba
inválido y no entró ningún paquete durante 3 días sin que nada lo dijera."""
from __future__ import annotations

import os
import re

PREFIJOS = ("MG_", "AEMET_", "METEOCAT_")
NUMERICAS = ("MG_APRS_PORT", "MG_APRS_RADIUS")
FILTRO = re.compile(r"^-?(r|p|b|o|d|e|g|u|t|s|a|m|f|q|os)/\S+$")


def _filtro_valido(filtro: str) -> bool:
    for t in filtro.split():
        if not FILTRO.match(t):
            return False
        if t.startswith("r/"):
            try:
                lat, lon, km = (float(x) for x in t.split("/")[1:4])
            except ValueError:
                return False
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < km <= 5000):
                return False
    return True


def avisos(env: dict | None = None) -> list[str]:
    """Lista de problemas (texto legible, con el nombre de la variable y sin su valor). Vacía si todo cuadra."""
    env = os.environ if env is None else env
    out = []
    for k in sorted(env):
        v = env[k]
        if k.startswith(PREFIJOS) and re.search(r"(^|\s)#", v):
            out.append(f"{k}: el valor lleva un comentario «#» pegado (Docker lo trata como parte del valor); pon el comentario en su propia línea del .env")
    filtro = (env.get("MG_APRS_FILTER") or "").split("#", 1)[0].strip()
    if filtro and not _filtro_valido(filtro):
        out.append("MG_APRS_FILTER: no es un filtro APRS-IS válido (p. ej. «r/41.49/2.36/60»); sin filtro válido no entra ningún paquete")
    for k in NUMERICAS:
        v = (env.get(k) or "").split("#", 1)[0].strip()
        if v and not v.isdigit():
            out.append(f"{k}: debe ser un número entero")
    return out
