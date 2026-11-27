"""Cuaderno de contactos (QSO/QSL) y perfiles anónimos con clave secreta, accesibles por API y MCP."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone

from .db import pool
from .radio import from_locator
from .qc import haversine_km

CALL_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?$|^[A-Z0-9/]{3,14}$")
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$", re.I)
MAX_QSOS = 20000
FIELDS = ["call", "qso_at", "band", "mode", "freq_mhz", "rst_sent", "rst_rcvd", "grid", "name", "notes", "qsl_sent", "qsl_rcvd"]
_SEL = "id, call, qso_at, band, mode, freq_mhz, rst_sent, rst_rcvd, grid, name, notes, qsl_sent, qsl_rcvd, servicios"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_profile() -> str:
    key = "mg_" + secrets.token_urlsafe(32)
    with pool().connection() as con:
        con.execute("INSERT INTO profiles (key_hash) VALUES (%s)", [_hash(key)])
    return key


def auth(key: str | None) -> str | None:
    if not key or len(key) > 200:
        return None
    h = _hash(key)
    with pool().connection() as con:
        return h if con.execute("SELECT 1 FROM profiles WHERE key_hash = %s", [h]).fetchone() else None


def get_settings(h: str) -> dict:
    with pool().connection() as con:
        return con.execute("SELECT settings FROM profiles WHERE key_hash = %s", [h]).fetchone()["settings"]


def put_settings(h: str, settings: dict) -> dict:
    import json
    if len(json.dumps(settings)) > 50_000:
        raise ValueError("Ajustes demasiado grandes")
    with pool().connection() as con:
        con.execute("UPDATE profiles SET settings = %s::jsonb WHERE key_hash = %s", [json.dumps(settings), h])
    return settings


def delete_profile(h: str) -> None:
    with pool().connection() as con:
        con.execute("DELETE FROM profiles WHERE key_hash = %s", [h])


def clean(q: dict) -> dict:
    """Valida y normaliza un QSO (lanza ValueError con mensaje en español)."""
    call = str(q.get("call", "")).strip().upper()
    if not CALL_RE.match(call):
        raise ValueError("Indicativo no válido")
    at = q.get("qso_at")
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if not isinstance(at, datetime):
        raise ValueError("Fecha/hora no válida")
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    grid = (q.get("grid") or "").strip()
    if grid and not GRID_RE.match(grid):
        raise ValueError("Locator no válido (p. ej. IN80 o IN80do)")
    freq = q.get("freq_mhz")
    freq = float(freq) if freq not in (None, "") else None
    return {"call": call, "qso_at": at, "band": (q.get("band") or None), "mode": (q.get("mode") or None), "freq_mhz": freq,
            "rst_sent": (q.get("rst_sent") or None), "rst_rcvd": (q.get("rst_rcvd") or None),
            "grid": grid.upper()[:2] + grid[2:4] + grid[4:6].lower() if grid else None, "name": (q.get("name") or None),
            "notes": (q.get("notes") or None)[:500] if q.get("notes") else None, "qsl_sent": bool(q.get("qsl_sent")),
            "qsl_rcvd": bool(q.get("qsl_rcvd"))}


def add_many(h: str, items: list[dict]) -> list[int]:
    with pool().connection() as con:
        n = con.execute("SELECT count(*) AS n FROM qsos WHERE key_hash = %s", [h]).fetchone()["n"]
        if n + len(items) > MAX_QSOS:
            raise ValueError(f"Máximo {MAX_QSOS} contactos por cuaderno")
        ids = []
        for q in items:
            r = con.execute(
                f"INSERT INTO qsos (key_hash, {', '.join(FIELDS)}) VALUES (%s, {', '.join('%s' for _ in FIELDS)}) RETURNING id",
                [h] + [q[f] for f in FIELDS]).fetchone()
            ids.append(r["id"])
    return ids


def list_qsos(h: str, q: str | None = None, band: str | None = None, mode: str | None = None, status: str | None = None,
              limit: int = 200, offset: int = 0) -> list[dict]:
    where, args = ["key_hash = %s"], [h]
    if q:
        where.append("(call ILIKE %s OR name ILIKE %s OR notes ILIKE %s)"); args += [f"%{q}%"] * 3
    if band:
        where.append("band = %s"); args.append(band)
    if mode:
        where.append("mode = %s"); args.append(mode)
    if status == "pending":
        where.append("NOT qsl_rcvd")
    elif status == "confirmed":
        where.append("qsl_rcvd")
    with pool().connection() as con:
        return con.execute(f"SELECT {_SEL} FROM qsos WHERE {' AND '.join(where)} ORDER BY qso_at DESC, id DESC LIMIT %s OFFSET %s",
                           args + [min(limit, 1000), offset]).fetchall()


def update(h: str, qid: int, patch: dict) -> bool:
    allowed = {k: v for k, v in patch.items() if k in ("qsl_sent", "qsl_rcvd", "notes", "name", "grid", "rst_sent", "rst_rcvd")}
    if not allowed:
        return False
    sets = ", ".join(f"{k} = %s" for k in allowed)
    with pool().connection() as con:
        cur = con.execute(f"UPDATE qsos SET {sets} WHERE id = %s AND key_hash = %s", list(allowed.values()) + [qid, h])
        return cur.rowcount > 0


def delete(h: str, qid: int) -> bool:
    with pool().connection() as con:
        return con.execute("DELETE FROM qsos WHERE id = %s AND key_hash = %s", [qid, h]).rowcount > 0


def stats(h: str, qth: dict | None = None) -> dict:
    rows = list_qsos(h, limit=1000)
    with pool().connection() as con:
        total = con.execute("SELECT count(*) AS n FROM qsos WHERE key_hash = %s", [h]).fetchone()["n"]
        by = lambda col: con.execute(f"SELECT {col} AS k, count(*) AS n FROM qsos WHERE key_hash = %s AND {col} IS NOT NULL GROUP BY {col} ORDER BY n DESC", [h]).fetchall()  # noqa: E731
        bands, modes = by("band"), by("mode")
        months = con.execute("SELECT to_char(qso_at, 'YYYY-MM') AS k, count(*) AS n FROM qsos WHERE key_hash = %s GROUP BY 1 ORDER BY 1 DESC LIMIT 12", [h]).fetchall()
        agg = con.execute("SELECT count(*) FILTER (WHERE qsl_rcvd) AS conf, count(*) FILTER (WHERE qsl_sent) AS sent, count(DISTINCT call) AS calls, "
                          "count(DISTINCT left(grid, 4)) FILTER (WHERE grid IS NOT NULL) AS grids FROM qsos WHERE key_hash = %s", [h]).fetchone()
    far = None
    if qth and qth.get("lat") is not None:
        ds = [(haversine_km(qth["lat"], qth["lon"], *p), r["call"]) for r in rows if r["grid"] and (p := _pos(r["grid"]))]
        far = max(ds) if ds else None
    return {"total": total, "confirmados": agg["conf"], "enviadas": agg["sent"], "indicativos": agg["calls"], "cuadros": agg["grids"],
            "por_banda": bands, "por_modo": modes, "por_mes": months,
            "mas_lejano": {"km": round(far[0]), "indicativo": far[1]} if far else None}


def _pos(grid: str):
    try:
        return from_locator(grid)
    except ValueError:
        return None


def worked_before(h: str, call: str, band: str | None = None, mode: str | None = None) -> list[dict]:
    with pool().connection() as con:
        return con.execute(f"SELECT {_SEL} FROM qsos WHERE key_hash = %s AND call = %s ORDER BY qso_at DESC LIMIT 20", [h, call.upper()]).fetchall()


# ---------- ADIF ----------
def to_adif(rows: list[dict], my_grid: str | None = None) -> str:
    def f(k, v):
        return f"<{k}:{len(str(v))}>{v}" if v not in (None, "") else ""
    out = ["Exportado por Radio Guilda", "<ADIF_VER:5>3.1.4<PROGRAMID:11>RadioGuilda<EOH>"]
    for q in rows:
        d = q["qso_at"].astimezone(timezone.utc)
        out.append("".join([f("CALL", q["call"]), f("QSO_DATE", d.strftime("%Y%m%d")), f("TIME_ON", d.strftime("%H%M")),
                            f("BAND", q["band"]), f("MODE", (q["mode"] or "").replace("-", "") if q["mode"] != "Otro" else ""),
                            f("FREQ", q["freq_mhz"]), f("RST_SENT", q["rst_sent"]), f("RST_RCVD", q["rst_rcvd"]), f("GRIDSQUARE", q["grid"]),
                            f("NAME", q["name"]), f("COMMENT", q["notes"]), f("QSL_SENT", "Y" if q["qsl_sent"] else "N"),
                            f("QSL_RCVD", "Y" if q["qsl_rcvd"] else "N"), f("MY_GRIDSQUARE", my_grid), "<EOR>"]))
    return "\n".join(out) + "\n"


_FIELD = re.compile(r"<([A-Za-z_0-9]+):(\d+)(?::[A-Za-z])?>", re.S)


def parse_adif(text: str, max_records: int = 20000) -> list[dict]:
    """Lee un ADIF (.adi) y devuelve registros normalizados; ignora los que no se pueden interpretar."""
    if len(text) > 3_000_000:
        raise ValueError("ADIF demasiado grande (máx. 3 MB)")
    m = re.search(r"<eoh>", text, re.I)
    body = text[m.end():] if m else text
    out = []
    for rec in re.split(r"<eor>", body, flags=re.I):
        d, pos = {}, 0
        while (mm := _FIELD.search(rec, pos)):
            n = int(mm.group(2))
            d[mm.group(1).upper()] = rec[mm.end():mm.end() + n]
            pos = mm.end() + n
        if not d.get("CALL"):
            continue
        try:
            date, tm = d.get("QSO_DATE", ""), (d.get("TIME_ON", "0000") + "00")[:6]
            at = datetime.strptime(date + tm, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            out.append(clean({"call": d["CALL"], "qso_at": at, "band": d.get("BAND", "").lower() or None, "mode": d.get("MODE") or None,
                              "freq_mhz": d.get("FREQ") or None, "rst_sent": d.get("RST_SENT"), "rst_rcvd": d.get("RST_RCVD"),
                              "grid": d.get("GRIDSQUARE") or None, "name": d.get("NAME"), "notes": d.get("COMMENT"),
                              "qsl_sent": d.get("QSL_SENT", "").upper() == "Y", "qsl_rcvd": d.get("QSL_RCVD", "").upper() == "Y"}))
        except (ValueError, TypeError):
            continue
        if len(out) >= max_records:
            break
    return out
