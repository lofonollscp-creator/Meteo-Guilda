"""Ingestor APRS-IS (solo lectura). Uso: python -m meteoguilda.ingest

Variables: MG_APRS_HOST (rotate.aprs2.net), MG_APRS_PORT (14580),
MG_APRS_FILTER (por defecto: círculo de MG_APRS_RADIUS km alrededor de cada lugar).
"""
from __future__ import annotations

import logging
import os
import socket
import time

from . import configuracion, db, store
from .aprs import parse_packet
from .places import PLACES
from .qc import range_ok

log = logging.getLogger("ingest")
HOST = os.environ.get("MG_APRS_HOST", "rotate.aprs2.net")
PORT = int(os.environ.get("MG_APRS_PORT", "14580"))
# Los filtros APRS-IS se combinan con OR: "t/w" traería TODOS los paquetes meteo del mundo.
# Por eso se pide solo un círculo por lugar (todo tipo de paquete) y se filtra el meteo localmente.
RADIUS_KM = int(os.environ.get("MG_APRS_RADIUS", "60"))


def filtro(valor: str | None) -> str:
    """Filtro APRS-IS a usar. Docker (`env_file`) deja los comentarios al final de la línea dentro del valor
    (`MG_APRS_FILTER=   # vacío = …` llegaba como filtro «# vacío = …» y el servidor no mandaba nada), así que se corta en el primer «#»."""
    limpio = (valor or "").split("#", 1)[0].strip()
    return limpio or " ".join(f"r/{p.lat:.3f}/{p.lon:.3f}/{RADIUS_KM}" for p in PLACES)


FILTER = filtro(os.environ.get("MG_APRS_FILTER"))


def run() -> None:
    for problema in configuracion.avisos():
        log.warning("configuración sospechosa: %s", problema)
    db.init_schema()
    backoff = 5
    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=30) as s:
                s.settimeout(120)
                f = s.makefile("rw", encoding="utf-8", errors="replace", newline="\n")
                f.write(f"user N0CALL pass -1 vers meteoguilda 0.1 filter {FILTER}\r\n")
                f.flush()
                log.info("conectado a %s:%s", HOST, PORT)
                backoff, last_purge, last_flush, buf = 5, time.time(), time.time(), []
                for line in f:
                    line = line.strip()
                    o = parse_packet(line) if line and not line.startswith("#") else None
                    if o and o.lat is not None and range_ok(o):
                        buf.append(o)
                    if buf and (len(buf) >= 50 or time.time() - last_flush > 10):
                        store.insert_many(buf)
                        buf, last_flush = [], time.time()
                    if time.time() - last_purge > 3600:
                        store.purge()
                        last_purge = time.time()
        except (OSError, TimeoutError) as e:
            log.warning("conexión perdida (%s); reintento en %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
