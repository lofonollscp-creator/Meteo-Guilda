"""«Ventanas» de Radio: cuándo conviene operar en las próximas 48 h, tramo a tramo de 3 h.

Junta cuatro cosas que hasta ahora estaban en pantallas distintas: HF (luz del día, línea gris y estado solar), tropo VHF/UHF (gradiente de refractividad
de los modelos), pasos de satélites y, para operar fuera (POTA/SOTA/móvil), el riesgo del tiempo para la antena. Todo es orientativo y determinista:
no hay predicción ionosférica real (para eso, VOACAP). Con `modo="fijo"` el campo no cuenta; con `modo="portatil"` el tiempo adverso baja la nota de todo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import astro, portable, routing

PASO_H = 3
MODOS = ("fijo", "portatil")
GRIS_MIN = 60          # minutos alrededor del orto/ocaso que cuentan como línea gris


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def luz(t: datetime, lat: float, lon: float) -> str:
    """'dia' | 'noche' | 'gris' (±1 h alrededor de la salida o la puesta del Sol, la mejor hora para DX en 80/40/30 m)."""
    for dia in (t.date() - timedelta(days=1), t.date(), t.date() + timedelta(days=1)):
        rise, _, sset = astro.sun_event(lat, lon, dia)
        for ev in (rise, sset):
            if ev and abs((t - ev).total_seconds()) <= GRIS_MIN * 60:
                return "gris"
    return "dia" if routing.is_daylight(t, lat, lon) else "noche"


def hf(l: str, kp: float | None, sfi: float | None) -> dict:
    """Nota 0-100 y bandas recomendadas según la luz y el estado solar actual (se supone estable en 48 h)."""
    alto = (sfi or 90) >= 150
    medio = (sfi or 90) >= 100
    if l == "dia":
        bandas = ["15 m", "12 m", "10 m", "17 m", "20 m"] if alto else ["20 m", "17 m", "15 m"] if medio else ["20 m", "30 m", "40 m"]
        base = 80 if alto else 65 if medio else 45
    elif l == "gris":
        bandas = ["40 m", "30 m", "80 m", "20 m"]
        base = 85 if medio else 75
    else:
        bandas = ["40 m", "80 m", "160 m"] if medio else ["40 m", "80 m"]
        base = 60 if medio else 55
    k = 1.0 if kp is None else 0.35 if kp >= 5 else 0.7 if kp >= 4 else 0.9 if kp >= 3 else 1.0
    return {"nota": _clamp(base * k), "bandas": bandas, "luz": l,
            "motivo": ("Tormenta geomagnética: HF degradado" if kp is not None and kp >= 5 else "Línea gris: la mejor hora para DX en bandas bajas" if l == "gris"
                       else "Con luz: bandas altas según el flujo solar" if l == "dia" else "De noche: bandas bajas")}


def tropo(cat: str | None, gradiente: float | None) -> dict:
    """Nota 0-100 de VHF/UHF a larga distancia a partir del gradiente de refractividad (N/km)."""
    if cat is None:
        return {"nota": 0, "cat": None, "motivo": "Sin datos"}
    nota = {"conducto": 95, "super-refracción": 75, "normal": 25, "sub-refracción": 10}.get(cat, 25)
    return {"nota": nota, "cat": cat, "gradiente": gradiente,
            "motivo": {"conducto": "Posible conducto: aperturas de 144/432 MHz a cientos de km", "super-refracción": "Super-refracción: alcance mayor de lo normal",
                       "normal": "Condiciones normales: solo alcance directo", "sub-refracción": "Sub-refracción: alcance reducido"}.get(cat, "")}


def sat(pasos: list[dict]) -> dict:
    """Nota 0-100 de los pasos que empiezan en el tramo: pesa la mejor elevación (≥ 60° es un pase excelente)."""
    if not pasos:
        return {"nota": 0, "pasos": []}
    mejor = max(p["max_el"] for p in pasos)
    return {"nota": _clamp(20 + mejor * 0.9), "pasos": [{"satelite": p["satelite"], "max_el": p["max_el"], "aos": p["aos"], "duracion_s": p["duracion_s"]}
                                                       for p in sorted(pasos, key=lambda p: -p["max_el"])[:4]]}


def campo(horas: list[dict]) -> dict:
    """Riesgo del tiempo para operar fuera en el tramo: el peor nivel de sus horas (0 verde, 1 ámbar, 2 rojo)."""
    if not horas:
        return {"nivel": 0, "motivos": []}
    nivel = max(h["nivel"] for h in horas)
    motivos = list(dict.fromkeys(m for h in horas for m in h["motivos"] if m != "De noche"))
    return {"nivel": nivel, "motivos": motivos[:3], "racha_kmh": max((h["racha_kmh"] or 0) for h in horas), "lluvia_mm": round(sum((h["lluvia_mm"] or 0) for h in horas), 1)}


def construir(lat: float, lon: float, *, ahora: datetime, horas_campo: list[dict], tropo_serie: dict | None, solar: dict, pasos: list[dict],
              modo: str = "fijo", horas: int = 48) -> dict:
    """Tramos de 3 h desde la próxima hora en punto, con una nota por capa y una global (`total`) según el modo."""
    modo = modo if modo in MODOS else "fijo"
    ini = ahora.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    off = timedelta(seconds=(tropo_serie or {}).get("utc_offset", 0))
    tr = {}
    for r in (tropo_serie or {}).get("serie", []):
        tr[(datetime.fromisoformat(r["time"]) - off).replace(tzinfo=timezone.utc)] = r
    por_hora = {datetime.fromisoformat(h["t"]).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0): h for h in horas_campo}
    tramos = []
    for k in range(horas // PASO_H):
        t0 = ini + timedelta(hours=k * PASO_H)
        t1 = t0 + timedelta(hours=PASO_H)
        centro = t0 + timedelta(hours=1, minutes=30)
        l = luz(centro, lat, lon)
        hfx = hf(l, solar.get("kp"), solar.get("sfi"))
        horas_t = [tr[x] for x in (t0 + timedelta(hours=i) for i in range(PASO_H)) if x in tr]
        peor = min(horas_t, key=lambda r: r["gradient"]) if horas_t else None
        tp = tropo(peor["cat"] if peor else None, peor["gradient"] if peor else None)
        st = sat([p for p in pasos if t0 <= datetime.fromisoformat(p["aos"]) < t1])
        cp = campo([por_hora[x] for x in (t0 + timedelta(hours=i) for i in range(PASO_H)) if x in por_hora])
        capas = [hfx["nota"], tp["nota"], st["nota"]]
        total = max(capas)
        if modo == "portatil":
            total = 0 if cp["nivel"] == 2 else _clamp(total * (0.6 if cp["nivel"] == 1 else 1))
        tramos.append({"desde": t0.isoformat(), "hasta": t1.isoformat(), "hf": hfx, "tropo": tp, "sat": st, "campo": cp, "total": total,
                       "mejor": max(("hf", "tropo", "sat"), key=lambda n: {"hf": hfx, "tropo": tp, "sat": st}[n]["nota"])})
    mejores = {n: [t["desde"] for t in sorted(tramos, key=lambda t: -t[n]["nota"])[:3] if t[n]["nota"] >= 40] for n in ("hf", "tropo", "sat")}
    mejores["general"] = [t["desde"] for t in sorted(tramos, key=lambda t: -t["total"])[:3] if t["total"] >= 40]
    return {"modo": modo, "tramos": tramos, "mejores": mejores, "solar": {"kp": solar.get("kp"), "sfi": solar.get("sfi")},
            "aviso": "Orientativo. HF usa el estado solar de ahora y la hora del día, no una predicción ionosférica (para eso, VOACAP). La tropo viene de modelos "
                     "y no ve los conductos de capa fina. Los pasos de satélite son cálculo orbital con TLE recientes."}
