"""Avisos oficiales (AEMET/Meteoalerta) de un punto. Son la capa de SEGURIDAD de la app: la IA nunca los contradice.

- Con `AEMET_API_KEY` (gratuita, https://opendata.aemet.es) se baja el último CAP de AEMET (polígonos de cada zona de aviso) y se devuelven los avisos
  vigentes cuyo polígono contiene el punto. Sin clave solo se devuelven los enlaces oficiales del sitio (no se inventa nada).
- Los enlaces salen siempre: AEMET/Meteocat/Météo-France según dónde esté el punto, y MeteoAlarm (toda Europa).
"""
from __future__ import annotations

import io
import asyncio
import logging
import os
import tarfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

log = logging.getLogger("avisos")
AEMET = "https://opendata.aemet.es/opendata/api/avisos_cap/ultimoelaborado/area/esp"
NS = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
NIVEL = {"minor": 1, "moderate": 1, "severe": 2, "extreme": 3}          # amarillo/naranja/rojo (CAP: Moderate=amarillo, Severe=naranja, Extreme=rojo)
COLOR = {1: "amarillo", 2: "naranja", 3: "rojo"}
_cache: tuple[float, list[dict]] = (0, [])
TTL = 900
REINTENTO_S = 300        # tras un fallo (p. ej. 429 de AEMET) no se vuelve a llamar hasta pasado este tiempo: antes cada visita reintentaba y empeoraba el límite
_hasta = 0.0             # no reintentar antes de este instante
_lock: asyncio.Lock | None = None


def in_polygon(lat: float, lon: float, ring: list[tuple[float, float]]) -> bool:
    """Punto en polígono (trazado de rayos). `ring` son pares (lat, lon)."""
    inside, n = False, len(ring)
    for i in range(n):
        (y1, x1), (y2, x2) = ring[i], ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def parse_ring(txt: str) -> list[tuple[float, float]]:
    out = []
    for pair in txt.split():
        a, _, b = pair.partition(",")
        try:
            out.append((float(a), float(b)))
        except ValueError:
            return []
    return out


def parse_cap(xml: bytes | str) -> list[dict]:
    """Avisos de un mensaje CAP 1.2: uno por zona (`area`) con sus polígonos. Prefiere el texto en español."""
    root = ET.fromstring(xml)
    out = []
    infos = root.findall("c:info", NS)
    infos.sort(key=lambda i: 0 if (i.findtext("c:language", "", NS) or "").lower().startswith("es") else 1)
    seen = set()
    for info in infos:
        sev = (info.findtext("c:severity", "", NS) or "").lower()
        for area in info.findall("c:area", NS):
            nombre = area.findtext("c:areaDesc", "", NS)
            key = (info.findtext("c:event", "", NS), nombre)
            if key in seen:
                continue
            seen.add(key)
            rings = [r for r in (parse_ring(p.text or "") for p in area.findall("c:polygon", NS)) if len(r) >= 3]
            out.append({"evento": info.findtext("c:event", "", NS), "titular": info.findtext("c:headline", "", NS), "zona": nombre, "severidad": sev,
                        "nivel": NIVEL.get(sev, 1), "desde": info.findtext("c:onset", "", NS) or info.findtext("c:effective", "", NS),
                        "hasta": info.findtext("c:expires", "", NS), "descripcion": info.findtext("c:description", "", NS),
                        "instruccion": info.findtext("c:instruction", "", NS), "poligonos": rings})
    return out


def vigente(a: dict, ahora: datetime) -> bool:
    try:
        hasta = datetime.fromisoformat(a["hasta"]) if a.get("hasta") else None
    except ValueError:
        return True
    return hasta is None or hasta.astimezone(timezone.utc) > ahora


async def _bajar(client: httpx.AsyncClient, key: str) -> list[dict]:
    r = await client.get(AEMET, headers={"api_key": key}, timeout=25)
    r.raise_for_status()
    url = r.json().get("datos")
    if not url:
        return []
    d = await client.get(url, timeout=40, follow_redirects=True)
    d.raise_for_status()
    avisos: list[dict] = []
    raw = d.content
    try:
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith((".xml", ".cap")):
                    try:
                        avisos += parse_cap(tf.extractfile(m).read())
                    except ET.ParseError:
                        continue
    except tarfile.TarError:
        avisos = parse_cap(raw)
    return avisos


async def todos(client: httpx.AsyncClient) -> list[dict] | None:
    """Avisos vigentes de AEMET (None sin clave o si nunca se pudo descargar; caché de 15 min).
    Si la descarga falla se sigue sirviendo la última lista buena y se espera 5 min (o lo que pida `Retry-After`) antes de reintentar."""
    global _cache, _hasta, _lock
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        return None
    ahora_s = time.time()
    if ahora_s - _cache[0] < TTL:
        return _cache[1]
    if ahora_s < _hasta:
        return _cache[1] if _cache[0] else None
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:                                    # una sola descarga a la vez; las demás peticiones esperan y reutilizan
        if time.time() - _cache[0] < TTL:
            return _cache[1]
        if time.time() < _hasta:
            return _cache[1] if _cache[0] else None
        try:
            lista = await _bajar(client, key)
        except (httpx.HTTPError, ValueError, ET.ParseError) as e:
            espera = REINTENTO_S
            r = getattr(e, "response", None)
            try:
                espera = max(60, min(3600, int(r.headers.get("retry-after")))) if r is not None and r.headers.get("retry-after") else espera
            except ValueError:
                pass
            _hasta = time.time() + espera
            log.warning("avisos AEMET: %s (se reintenta en %d s)", str(e).split("\n")[0][:140], espera)
            return _cache[1] if _cache[0] else None
        ahora = datetime.now(timezone.utc)
        _cache = (time.time(), [a for a in lista if vigente(a, ahora)])
        _hasta = 0.0
        return _cache[1]


def enlaces(lat: float, lon: float) -> list[dict]:
    out = []
    if 35 <= lat <= 44 and -9.6 <= lon <= 4.4:
        out.append({"nombre": "AEMET", "url": "https://www.aemet.es/es/eltiempo/prediccion/avisos"})
    if 40.5 <= lat <= 42.9 and 0.15 <= lon <= 3.35:
        out.append({"nombre": "Meteocat", "url": "https://www.meteo.cat/prediccio/avisos"})
    if 41.3 <= lat <= 51.2 and -5.2 <= lon <= 9.6:
        out.append({"nombre": "Météo-France Vigilance", "url": "https://vigilance.meteofrance.fr"})
    out.append({"nombre": "MeteoAlarm (Europa)", "url": "https://meteoalarm.org"})
    return out


def para_punto(avisos: list[dict] | None, lat: float, lon: float) -> dict:
    """Avisos que afectan al punto + enlaces oficiales. `disponible` es False cuando no hay clave o falló la descarga (no es lo mismo que «sin avisos»)."""
    res = {"disponible": avisos is not None, "avisos": [], "enlaces": enlaces(lat, lon), "fuente": "AEMET (Meteoalerta)"}
    for a in avisos or []:
        if any(in_polygon(lat, lon, r) for r in a["poligonos"]):
            res["avisos"].append({k: v for k, v in a.items() if k != "poligonos"} | {"color": COLOR.get(a["nivel"], "amarillo")})
    res["avisos"].sort(key=lambda a: -a["nivel"])
    return res
