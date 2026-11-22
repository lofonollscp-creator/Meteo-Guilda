"""Búsqueda libre de lugares (geocodificador de Open-Meteo, con caché)."""
from __future__ import annotations

import time

import httpx

URL = "https://geocoding-api.open-meteo.com/v1/search"
_cache: dict[str, tuple[float, list[dict]]] = {}


async def search(client: httpx.AsyncClient, q: str, count: int = 8, lang: str = "es") -> list[dict]:
    q = q.strip()
    if len(q) < 2:
        return []
    key = f"{lang}:{count}:{q.lower()}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    r = await client.get(URL, params={"name": q, "count": count, "language": lang, "format": "json"}, timeout=15)
    r.raise_for_status()
    out = []
    for x in r.json().get("results", []):
        out.append({"id": x.get("id"), "name": x["name"], "admin1": x.get("admin1"), "admin2": x.get("admin2"),
                    "country": x.get("country"), "cc": x.get("country_code"), "lat": round(x["latitude"], 5),
                    "lon": round(x["longitude"], 5), "ele": x.get("elevation"), "pop": x.get("population")})
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = (time.time(), out)
    return out
