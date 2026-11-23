"""Planificador de actividades: puntúa cada tramo de 3 h (0-100) con reglas sencillas y devuelve las mejores ventanas.
No inventa datos: usa las medias entre modelos del resumen (temperatura, lluvia, viento, rachas, nubes, humedad, CAPE)."""
from __future__ import annotations

from datetime import datetime, timedelta

# t: rango de temperatura cómodo (°C); pen_t: puntos por °C fuera de rango; lluvia: puntos por mm/3 h; viento/racha: km/h cómodos y puntos por km/h de más;
# nubes/hum: máximos cómodos (%); horas: franja del día (hora local, inicio-fin; si inicio > fin cruza la medianoche)
ACTIVITIES = {
    "senderismo": {"label": "Senderismo", "t": (4, 24), "pen_t": 3, "lluvia": 70, "viento": 25, "pen_v": 1.2, "racha": 45, "pen_r": 1.0, "horas": (7, 19), "cape": 300},
    "ciclismo": {"label": "Ciclismo", "t": (8, 28), "pen_t": 3, "lluvia": 80, "viento": 18, "pen_v": 2.5, "racha": 35, "pen_r": 1.5, "horas": (8, 20), "cape": 300},
    "dron": {"label": "Vuelo de dron", "t": (-5, 38), "pen_t": 2, "lluvia": 120, "viento": 14, "pen_v": 4, "racha": 22, "pen_r": 3, "horas": (8, 20), "cape": 200},
    "colada": {"label": "Tender la ropa", "t": (10, 40), "pen_t": 3, "lluvia": 150, "viento": 60, "pen_v": 0.3, "racha": 80, "pen_r": 0.2, "horas": (9, 19), "hum": 70, "pen_h": 1.2},
    "estrellas": {"label": "Observar las estrellas", "t": (-5, 30), "pen_t": 1, "lluvia": 120, "viento": 30, "pen_v": 1, "racha": 50, "pen_r": 0.5, "horas": (21, 5), "nubes": 15, "pen_n": 1.4},
    "playa": {"label": "Playa y baño", "t": (22, 36), "pen_t": 4, "lluvia": 100, "viento": 22, "pen_v": 2, "racha": 35, "pen_r": 1.5, "horas": (10, 19), "cape": 300},
}


def _in_hours(h: int, win: tuple[int, int]) -> bool:
    a, b = win
    return a <= h < b if a < b else (h >= a or h < b)


def score_step(step: dict, act: dict) -> tuple[int, list[str]]:
    """(puntuación 0-100, motivos de las penalizaciones) de un tramo del resumen."""
    s, why = 100.0, []
    g = lambda var, f="mean": (step.get(var) or {}).get(f)  # noqa: E731
    t, rain, wind, gust = g("temperature_2m"), g("precipitation") or 0, (g("wind_speed_10m") or 0) * 3.6, (g("wind_gusts_10m", "max") or 0) * 3.6
    if t is not None:
        lo, hi = act["t"]
        d = lo - t if t < lo else t - hi if t > hi else 0
        if d:
            s -= d * act["pen_t"]; why.append(f"{'frío' if t < lo else 'calor'} ({t:.0f} °C)")
    if rain > 0.05:
        s -= min(70, rain * act["lluvia"]); why.append(f"lluvia ({rain:.1f} mm)")
    if wind > act["viento"]:
        s -= (wind - act["viento"]) * act["pen_v"]; why.append(f"viento ({wind:.0f} km/h)")
    if gust > act["racha"]:
        s -= (gust - act["racha"]) * act["pen_r"]; why.append(f"rachas ({gust:.0f} km/h)")
    cape = g("cape", "max")
    if act.get("cape") and cape and cape > act["cape"]:
        s -= 30; why.append("riesgo de tormenta")
    if act.get("nubes") is not None:
        c = g("cloud_cover") or 0
        if c > act["nubes"]:
            s -= (c - act["nubes"]) * act["pen_n"]; why.append(f"nubes ({c:.0f} %)")
    if act.get("hum") is not None:
        hmd = g("relative_humidity_2m")
        if hmd is not None and hmd > act["hum"]:
            s -= (hmd - act["hum"]) * act["pen_h"]; why.append(f"humedad ({hmd:.0f} %)")
    return max(0, min(100, round(s))), why


def _end(start_iso: str) -> str:
    """Fin de un tramo de 3 h que empieza en `start_iso`."""
    return (datetime.fromisoformat(start_iso) + timedelta(hours=3)).isoformat(timespec="minutes")


def plan(summary: dict, activity: str, min_score: int = 65, top: int = 3) -> dict:
    if activity not in ACTIVITIES:
        raise ValueError(f"Actividad desconocida. Opciones: {', '.join(ACTIVITIES)}")
    act = ACTIVITIES[activity]
    steps = summary["steps"]
    rows = []
    for st in steps:
        dt = datetime.fromisoformat(st["time"])
        ok = _in_hours(dt.hour, act["horas"])
        sc, why = score_step(st, act)
        rows.append({"time": st["time"], "ok": ok, "score": sc, "why": why, "step": st})
    windows, run = [], []
    for r in rows + [{"ok": False, "score": 0}]:
        if r["ok"] and r["score"] >= min_score:
            run.append(r)
        elif run:
            windows.append(run); run = []
    out = []
    for w in windows:
        temps = [(x["step"].get("temperature_2m") or {}).get("mean") for x in w]
        temps = [x for x in temps if x is not None]
        out.append({"desde": w[0]["time"], "hasta": _end(w[-1]["time"]),
                    "horas": len(w) * 3, "puntuacion": round(sum(x["score"] for x in w) / len(w)),
                    "temp_c": [round(min(temps)), round(max(temps))] if temps else None,
                    "viento_kmh_max": round(max(((x["step"].get("wind_speed_10m") or {}).get("mean") or 0) * 3.6 for x in w)),
                    "lluvia_mm": round(sum((x["step"].get("precipitation") or {}).get("mean") or 0 for x in w), 1)})
    out.sort(key=lambda w: (-w["puntuacion"], -w["horas"], w["desde"]))
    now = rows[0] if rows else None
    return {"actividad": activity, "nombre": act["label"], "ahora": {"puntuacion": now["score"], "motivos": now["why"], "en_franja": now["ok"]} if now else None,
            "ventanas": out[:top], "criterio": f"Mejores tramos de 3 h con puntuación ≥ {min_score}/100 en la franja habitual ({act['horas'][0]}–{act['horas'][1]} h).",
            "modelos": len(summary.get("models", []))}
