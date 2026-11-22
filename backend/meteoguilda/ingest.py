"""Ingestor APRS-IS (solo lectura). Uso: python -m meteoguilda.ingest

Variables: MG_APRS_HOST (rotate.aprs2.net), MG_APRS_PORT (14580),
MG_APRS_FILTER (por defecto: weather en un círculo sobre España peninsular).
"""
from __future__ import annotations

import logging
import os
import socket
import time

from . import store
from .aprs import parse_packet
from .qc import range_ok

log = logging.getLogger("ingest")
HOST = os.environ.get("MG_APRS_HOST", "rotate.aprs2.net")
PORT = int(os.environ.get("MG_APRS_PORT", "14580"))
# t/w = paquetes meteo; r/lat/lon/km = círculo (centro de España, 700 km)
FILTER = os.environ.get("MG_APRS_FILTER", "r/40.2/-3.7/700 t/w")


def run() -> None:
    con = store.connect()
    backoff = 5
    while True:
        try:
            with socket.create_connection((HOST, PORT), timeout=30) as s:
                s.settimeout(120)
                f = s.makefile("rw", encoding="utf-8", errors="replace", newline="\n")
                f.write(f"user N0CALL pass -1 vers meteoguilda 0.1 filter {FILTER}\r\n")
                f.flush()
                log.info("conectado a %s:%s", HOST, PORT)
                backoff, n, last_purge = 5, 0, time.time()
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    o = parse_packet(line)
                    if o and range_ok(o):
                        store.insert(con, o)
                        n += 1
                        if n % 50 == 0:
                            con.commit()
                    if time.time() - last_purge > 3600:
                        store.purge(con)
                        last_purge = time.time()
        except (OSError, TimeoutError) as e:
            log.warning("conexión perdida (%s); reintento en %ss", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
