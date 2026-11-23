"""Contraste entre la previsión de los modelos y lo que miden AHORA las estaciones reales cercanas (APRS, METAR, XEMA).

Los modelos dan una media y un rango; las estaciones dicen si esa media encaja con la realidad de hoy en la zona. Si discrepan, la confianza de
las primeras horas baja y la IA lo ve. No se «corrigen» las cifras de los modelos: se muestran las dos cosas, para que se vea qué manda.
"""
from __future__ import annotations

import statistics

RADIO_KM = 30            # más lejos, la estación ya no representa el punto
UMBRALES = {"temp": (1.5, 3.0), "viento": (8, 15)}     # (acuerdo alto, acuerdo medio) en °C y km/h
ORDEN = ["alto", "medio", "bajo"]


def _par(estaciones: list[dict], clave: str, modelo: float | None, tipo: str) -> dict | None:
    xs = [s[clave] for s in estaciones if s.get(clave) is not None]
    if not xs or modelo is None:
        return None
    obs = statistics.median(xs)
    dif = round(obs - modelo, 1)
    lo, med = UMBRALES[tipo]
    return {"obs": round(obs, 1), "modelo": round(modelo, 1), "dif": dif, "acuerdo": "alto" if abs(dif) <= lo else "medio" if abs(dif) <= med else "bajo", "n": len(xs)}


def contrastar(ctx: dict) -> dict | None:
    """None si no hay estaciones cercanas o datos de los modelos."""
    tramos = ctx.get("tramos") or []
    cerca = [s for s in (ctx.get("estaciones_cercanas") or []) if (s.get("dist_km") or 0) <= RADIO_KM]
    if not tramos or not cerca:
        return None
    t0 = tramos[0]
    temp = _par(cerca, "temp_c", (t0.get("temp_c") or {}).get("mean"), "temp")
    viento = _par(cerca, "viento_kmh", (t0.get("viento_kmh") or {}).get("mean"), "viento")
    partes = [p for p in (temp, viento) if p]
    if not partes:
        return None
    acuerdo = max((p["acuerdo"] for p in partes), key=ORDEN.index)
    fuentes = sorted({s.get("fuente", "APRS") for s in cerca})
    txt = {"alto": "Las estaciones reales coinciden con los modelos.",
           "medio": "Las estaciones reales se desvían algo de los modelos: fíate algo menos de las primeras horas.",
           "bajo": "Las estaciones reales se desvían claramente de los modelos: manda lo observado en las próximas horas."}[acuerdo]
    return {"estaciones": len(cerca), "fuentes": fuentes, "temp": temp, "viento": viento, "acuerdo": acuerdo, "nota": txt}


def aplicar(ctx: dict) -> dict:
    """Añade `contraste` al contexto y rebaja la confianza de las primeras horas si las estaciones no encajan con los modelos."""
    c = contrastar(ctx)
    if not c:
        return ctx
    ctx["contraste"] = c
    if c["acuerdo"] != "alto":
        baja = {"alta": "media", "media": "baja"} if c["acuerdo"] == "medio" else {"alta": "baja", "media": "baja"}
        for r in ctx["tramos"][:3]:
            r["confianza"] = baja.get(r.get("confianza"), r.get("confianza"))
    return ctx
