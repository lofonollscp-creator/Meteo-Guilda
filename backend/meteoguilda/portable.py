"""Operación portátil (SOTA/POTA/campo): mejores ventanas y alerta de tormenta para antenas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import routing

# (ámbar, rojo) pensados para mástiles y antenas de campo
GUST = (40, 60)       # km/h
RAIN = (0.5, 3.0)     # mm/h
CAPE = (500, 1500)    # J/kg, con lluvia


def assess_hour(w: dict, daylight: bool) -> tuple[int, list[str]]:
    lvl, why = 0, []

    def bump(l, t):
        nonlocal lvl
        lvl = max(lvl, l); why.append(t)

    gust = (w.get("gust") or 0) * 3.6
    rain, cape, temp = w.get("rain") or 0, w.get("cape") or 0, w.get("temp")
    if gust >= GUST[1]: bump(2, f"Rachas de {round(gust)} km/h: no montes mástil")
    elif gust >= GUST[0]: bump(1, f"Rachas de {round(gust)} km/h: asegura tirantes")
    if rain >= RAIN[1]: bump(2, f"Lluvia fuerte ({rain:.1f} mm/h)")
    elif rain >= RAIN[0]: bump(1, f"Lluvia ({rain:.1f} mm/h)")
    if cape >= CAPE[0] and rain >= 0.1:
        bump(2 if cape >= CAPE[1] else 1, "Riesgo de tormenta: desconecta antenas y no operes al aire libre")
    if temp is not None and (temp <= -5 or temp >= 35):
        bump(1, f"Temperatura extrema ({round(temp)} °C)")
    if not daylight:
        bump(1, "De noche")
    return lvl, why


def window(hourly: dict, lat: float, lon: float, now: datetime | None = None, hours: int = 48) -> dict:
    """Riesgo hora a hora y tramos continuos de nivel 0 (≥ 2 h) para las próximas `hours` horas."""
    now = now or datetime.now(timezone.utc)
    ut = routing._utc_times(hourly)
    rows = []
    for i, t in enumerate(ut[:hours]):
        w = routing.at(hourly, t, ut)
        lvl, why = assess_hour(w, routing.is_daylight(t, lat, lon))
        rows.append({"t": t.isoformat(), "nivel": lvl, "motivos": why, "temp_c": _r(w["temp"]), "racha_kmh": _r(w["gust"], 3.6),
                     "lluvia_mm": _r(w["rain"], 1, 1), "nubes_pct": _r(w["cloud"]), "cape_jkg": _r(w["cape"])})
    runs, cur = [], None
    for r in rows:
        if r["nivel"] == 0:
            cur = cur or {"desde": r["t"], "horas": 0}
            cur["hasta"] = (datetime.fromisoformat(r["t"]) + timedelta(hours=1)).isoformat(); cur["horas"] += 1
        elif cur:
            runs.append(cur); cur = None
    if cur:
        runs.append(cur)
    best = sorted([r for r in runs if r["horas"] >= 2], key=lambda r: -r["horas"])[:3]
    storm = [r["t"] for r in rows if any("tormenta" in m for m in r["motivos"])]
    return {"horas": rows, "mejores_ventanas": sorted(best, key=lambda r: r["desde"]), "horas_con_tormenta": storm,
            "aviso": "Orientativo: modelos globales; en montaña las tormentas locales pueden adelantarse."}


def _r(v, mult=1, nd=0):
    return None if v is None else (round(v * mult, nd) if nd else round(v * mult))
