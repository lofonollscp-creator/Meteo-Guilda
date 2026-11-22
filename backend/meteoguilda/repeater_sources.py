"""Fuentes de repetidores: BrandMeister (API oficial) y HearHam (listado agregado).

Región: península ibérica, Baleares, Andorra y Francia (prefijos EA/CT/C3/F…).
No se inventa nada: solo se normalizan y fusionan datos publicados por esas fuentes.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from .db import pool

log = logging.getLogger("repeaters")
BM_URL = "https://api.brandmeister.network/v2/device"
HH_URL = "https://hearham.com/api/repeaters/v1"
BBOX = (35.0, -10.8, 51.5, 8.6)  # sur, oeste, norte, este
PREFIXES = ("EA", "EB", "EC", "ED", "EE", "EF", "EG", "EH", "AM", "AN", "AO", "CT", "CS", "CQ", "CR", "CU", "C3", "F", "TK", "TM")
SKIP_GROUPS = {"IRLP", "Allstar", "EchoLink"}   # nodos de internet, no repetidores
BANDS = [("10m", 28, 29.7), ("6m", 50, 54), ("4m", 70, 70.5), ("2m", 144, 146), ("70cm", 430, 440), ("23cm", 1240, 1300)]


def band_of(f: float) -> str | None:
    return next((b for b, lo, hi in BANDS if lo <= f <= hi), None)


def in_region(call: str, lat, lon) -> bool:
    if lat is None or lon is None:
        return False
    s, w, n, e = BBOX
    return s <= lat <= n and w <= lon <= e and call.upper().startswith(PREFIXES)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm_bm(d: dict, now: datetime | None = None) -> dict | None:
    """Dispositivo BrandMeister -> repetidor. Descarta hotspots, inactivos y fuera de región."""
    call = str(d.get("callsign") or "").strip().upper()
    tx, rx = _f(d.get("tx")), _f(d.get("rx"))
    lat, lon = _f(d.get("lat")), _f(d.get("lng"))
    if not call or not tx or not rx or not in_region(call, lat, lon):
        return None
    shift = round((rx - tx) * 1000)
    pep, agl = _f(d.get("pep")), _f(d.get("agl"))
    if int(d.get("id") or 0) >= 10_000_000 or (shift == 0 and (pep or 0) < 10):
        return None   # hotspots personales (ID de 8-9 cifras) o simplex de baja potencia
    try:
        seen = datetime.strptime(d["last_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, TypeError):
        seen = None
    now = now or datetime.now(timezone.utc)
    if not seen or now - seen > timedelta(days=120):
        return None
    return {"name": (d.get("city") or call).strip(), "callsign": call, "band": band_of(tx), "freq_mhz": round(tx, 5),
            "shift_khz": shift, "tone_hz": None, "mode": "DMR", "network": "BrandMeister", "color_code": d.get("colorcode"),
            "dmr_id": int(d["id"]), "power_w": pep, "height_m": agl if agl and agl > 0 else 10, "range_km": 40,
            "city": (d.get("city") or "").strip() or None, "lat": lat, "lon": lon, "last_seen": seen, "website": None,
            "source": "BrandMeister"}


def _mode(m: str) -> str | None:
    u = (m or "").upper()
    if u.startswith("DMR"):
        return "DMR"
    if "D-STAR" in u or "DSTAR" in u:
        return "D-STAR"
    if "YSF" in u or "C4FM" in u:
        return "C4FM"
    if u.startswith("FM"):
        return "FM"
    return None


def norm_hh(d: dict) -> dict | None:
    """Entrada de HearHam -> repetidor (frecuencias en Hz, tonos en 'encode')."""
    call = str(d.get("callsign") or "").strip().upper()
    lat, lon = _f(d.get("latitude")), _f(d.get("longitude"))
    f = _f(d.get("frequency"))
    mode = _mode(d.get("mode", ""))
    if not call or not f or not mode or d.get("group") in SKIP_GROUPS or d.get("operational") == 0 or d.get("restriction"):
        return None
    if not in_region(call, lat, lon):
        return None
    f_mhz = f / 1e6
    enc = str(d.get("encode") or "")
    cc = int(m.group(1)) if (m := re.fullmatch(r"CC(\d+)", enc.strip(), re.I)) else None
    tone = _f(enc) if cc is None else None
    desc = d.get("description") or ""
    url = re.search(r"https?://\S+", desc)
    off = _f(d.get("offset")) or 0
    power = _f(d.get("power"))
    return {"name": (d.get("city") or call).strip(), "callsign": call, "band": band_of(f_mhz), "freq_mhz": round(f_mhz, 5),
            "shift_khz": round(off / 1000), "tone_hz": tone if tone and tone > 0 else None, "mode": mode, "network": None,
            "color_code": cc, "dmr_id": None, "power_w": power, "height_m": 10, "range_km": 40,
            "city": (d.get("city") or "").strip() or None, "lat": lat, "lon": lon, "last_seen": None,
            "website": url.group(0).rstrip(".,)") if url else None, "source": "HearHam"}


def merge(bm: list[dict], hh: list[dict]) -> list[dict]:
    """Une ambas listas: mismo indicativo y salida a ≤ 12,5 kHz = mismo repetidor (gana BrandMeister)."""
    out: dict[tuple, dict] = {}
    for r in bm:
        out[(r["callsign"], r["freq_mhz"])] = dict(r)
    for r in hh:
        match = next((v for (c, f), v in out.items() if c == r["callsign"] and abs(f - r["freq_mhz"]) <= 0.0125), None)
        if match:
            match["website"] = match["website"] or r["website"]
            match["tone_hz"] = match["tone_hz"] or r["tone_hz"]
            if r["source"] not in match["source"]:
                match["source"] += "+" + r["source"]
        elif (r["callsign"], r["freq_mhz"]) not in out:
            out[(r["callsign"], r["freq_mhz"])] = dict(r)
    return list(out.values())


_COLS = ["name", "callsign", "band", "freq_mhz", "shift_khz", "tone_hz", "mode", "network", "color_code", "dmr_id",
         "power_w", "height_m", "range_km", "city", "last_seen", "website", "source"]


def store_all(rows: list[dict]) -> int:
    """Upsert por (callsign, frecuencia) y retirada de lo que ya no figura en las fuentes (no toca datos manuales)."""
    if not rows:
        return 0
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _COLS if c not in ("callsign", "freq_mhz")) + ", geom = EXCLUDED.geom, updated_at = now()"
    q = (f"INSERT INTO repeaters ({', '.join(_COLS)}, geom) VALUES ({', '.join('%s' for _ in _COLS)}, "
         "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) ON CONFLICT (callsign, freq_mhz) DO UPDATE SET " + sets)
    with pool().connection() as con, con.cursor() as cur:
        cur.execute("SELECT now() AS t")
        t0 = cur.fetchone()["t"]
        cur.executemany(q, [[r[c] for c in _COLS] + [r["lon"], r["lat"]] for r in rows])
        cur.execute("DELETE FROM repeaters WHERE source IN ('BrandMeister','HearHam','BrandMeister+HearHam') AND updated_at < %s", [t0])
    return len(rows)


async def sync(client: httpx.AsyncClient) -> dict:
    bm_raw = (await client.get(BM_URL, timeout=120)).json()
    hh_raw = (await client.get(HH_URL, timeout=120)).json()
    now = datetime.now(timezone.utc)
    bm = [r for d in bm_raw if (r := norm_bm(d, now))]
    hh = [r for d in hh_raw if (r := norm_hh(d))]
    rows = merge(bm, hh)
    n = await asyncio.to_thread(store_all, rows)
    log.info("repetidores: BM %s + HH %s -> %s", len(bm), len(hh), n)
    return {"brandmeister": len(bm), "hearham": len(hh), "total": n}


def needs_sync(max_age_days: int = 7) -> bool:
    with pool().connection() as con:
        r = con.execute("SELECT count(*) AS n, max(updated_at) AS u FROM repeaters WHERE source LIKE '%%BrandMeister%%' OR source LIKE '%%HearHam%%'").fetchone()
    return r["n"] == 0 or r["u"] is None or datetime.now(timezone.utc) - r["u"] > timedelta(days=max_age_days)
