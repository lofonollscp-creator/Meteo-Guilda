"""Planificador de salidas por tramos: semáforo de riesgo por tramo, mejor ventana horaria y equipo sugerido, para monte, carretera y mar.

Todo con REGLAS explícitas y auditables (ver docs/decisiones.md, «Riesgo de rutas: reglas, no IA»): la IA no interviene en los niveles.
- monte y carretera: parten de la ruta con el tiempo a la hora de paso (routing.plan) y añaden reglas propias del perfil
  (cota de nieve frente a la altitud del tramo, sensación térmica, niebla en cotas altas, tormentas por la tarde; hielo, niebla y viento lateral en carretera).
- mar: tramos en línea recta entre los puntos, a la velocidad del barco, con oleaje (modelo marino) y viento/tormenta (consenso de modelos).
Niveles: 0 verde (favorable), 1 ámbar (precaución), 2 rojo (desaconsejable). No sustituye a los avisos oficiales ni al criterio de quien sale."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from . import mar as mar_mod
from . import openmeteo, routing

COLOR = ["verde", "ámbar", "rojo"]
TEXTO = ["Favorable", "Precaución", "Desaconsejable"]
PERFILES = {"monte": "foot", "carretera": "car", "mar": "sea"}
NUDO_KMH = 1.852


def sensacion(temp: float | None, viento_kmh: float | None) -> float | None:
    """Sensación térmica por viento (fórmula de la AEMET/NOAA, válida con T ≤ 10 °C y viento > 4,8 km/h)."""
    if temp is None or viento_kmh is None or temp > 10 or viento_kmh <= 4.8:
        return temp
    v = viento_kmh ** 0.16
    return round(13.12 + 0.6215 * temp - 11.37 * v + 0.3965 * temp * v, 1)


def reglas_extra(perfil: str, m: dict, ele: float | None, hora_local: int | None, de_noche: bool) -> tuple[int, list[str]]:
    """Reglas propias de cada perfil sobre una muestra (`m` = meteo de la muestra en km/h, mm/h, cm/h). Devuelve (nivel, motivos)."""
    lv, why = 0, []

    def sube(n: int, txt: str):
        nonlocal lv
        lv = max(lv, n)
        why.append(txt)
    t, racha, viento = m.get("temp_c"), m.get("racha_kmh") or 0, m.get("viento_kmh") or 0
    lluvia, nieve, iso, cape = m.get("lluvia_mm") or 0, m.get("nieve_cm") or 0, m.get("isocero_m"), m.get("cape_jkg") or 0
    nubes, hr = m.get("nubes_pct") or 0, m.get("humedad_pct")
    if perfil == "monte":
        if iso is not None and ele is not None and iso < ele + 200:
            if lluvia + nieve >= 0.2:
                sube(2 if iso < ele else 1, f"Cota de nieve ({round(iso)} m) en la altitud del tramo ({round(ele)} m) con precipitación")
            elif t is not None and t <= 0:
                sube(1, f"Isocero ({round(iso)} m) por debajo del tramo: suelo helado probable")
        st = sensacion(t, viento)
        if st is not None and st <= -15:
            sube(2, f"Sensación térmica de {round(st)} °C")
        elif st is not None and st <= -7:
            sube(1, f"Sensación térmica de {round(st)} °C")
        if ele is not None and ele >= 1500 and nubes >= 95 and (hr or 0) >= 95:
            sube(1, "Niebla probable en cotas altas (orientación difícil)")
        if cape >= 500 and hora_local is not None and 13 <= hora_local <= 20:
            sube(2 if cape >= 1200 else 1, "Tormentas posibles por la tarde: termina antes o ten plan de escape")
    elif perfil == "carretera":
        if t is not None and t <= 2 and (hr or 0) >= 90 and lluvia < 0.1:
            sube(1, "Posible hielo o escarcha en el asfalto")
        if (hr or 0) >= 98 and viento < 10:
            sube(1, "Niebla probable: reduce la velocidad")
        if racha >= 70:
            sube(1, f"Rachas de {round(racha)} km/h: viento lateral (furgonetas, caravanas, motos)")
        if de_noche and (lluvia >= 1 or nieve > 0):
            sube(1, "De noche y con precipitación")
    return lv, why


def _equipo(perfil: str, filas: list[dict], extras: dict) -> list[str]:
    """Equipo sugerido según lo peor que se espera en la ruta (reglas simples y comprobables)."""
    peor = lambda k, f=max: f((r["meteo"].get(k) or 0 for r in filas), default=0)  # noqa: E731
    tmin = min((r["meteo"]["temp_c"] for r in filas if r["meteo"].get("temp_c") is not None), default=None)
    tmax = max((r["meteo"]["temp_c"] for r in filas if r["meteo"].get("temp_c") is not None), default=None)
    lluvia, racha, nieve = peor("lluvia_mm"), peor("racha_kmh"), peor("nieve_cm")
    noche = any(r.get("de_noche") for r in filas)
    eq: list[str] = []
    if perfil == "mar":
        eq += ["Chaleco salvavidas para cada persona", "Emisora VHF marina (canal 16) y teléfono en funda estanca", "Parte costero de AEMET antes de salir"]
        if lluvia >= 0.3 or peor("ola_m") >= 1:
            eq.append("Ropa de agua")
        if racha >= 37:
            eq.append("Rizos o vela de capa preparados (viento de 20 nudos o más)")
        if noche:
            eq.append("Luces de navegación y linterna")
        if tmax is not None and tmax >= 26:
            eq.append("Agua de sobra, gorra y crema solar")
        return eq
    if perfil == "carretera":
        if nieve > 0 or (tmin is not None and tmin <= 1 and lluvia > 0):
            eq.append("Cadenas o neumáticos de invierno, rascador y manta")
        if any("Niebla" in m for r in filas for m in r["motivos"]):
            eq.append("Antinieblas revisadas y más distancia de seguridad")
        if racha >= 70:
            eq.append("Evita remolques o cargas altas en la baca")
        eq.append("Depósito lleno y teléfono cargado")
        if extras.get("repetidores"):
            eq.append(f"Radio de 2 m/70 cm: {extras['repetidores']} repetidores cubren parte del recorrido")
        return eq
    eq += ["Agua y algo de comida", "Mapa o track sin conexión y batería de repuesto", "Botiquín y manta térmica"]
    if lluvia >= 0.3:
        eq.append("Chubasquero y funda para la mochila")
    if racha >= 40 or (tmin is not None and tmin <= 5):
        eq.append("Cortavientos y capa de abrigo")
    if any("Cota de nieve" in m or "helado" in m for r in filas for m in r["motivos"]) or nieve > 0:
        eq.append("Crampones o microcrampones, guantes y gorro")
    if tmax is not None and tmax >= 28:
        eq.append("Un litro de agua más, gorra y crema solar")
    if noche:
        eq.append("Frontal con pilas de repuesto")
    if any("Tormentas" in m for r in filas for m in r["motivos"]):
        eq.append("Plan de retirada antes de las 14 h y aléjate de crestas si truena")
    eq.append("Emisora de 2 m/70 cm" + (f" ({extras['repetidores']} repetidores cubren parte del recorrido)" if extras.get("repetidores") else "") + " y baliza APRS si la tienes")
    return eq


def _hora_local(iso: str | None, offset_s: int) -> int | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso)
    return ((t.astimezone(timezone.utc) + timedelta(seconds=offset_s)).hour) if t.tzinfo else t.hour


def _tramos(filas: list[dict]) -> list[dict]:
    """Tramo i = de la muestra i a la i+1, con el peor nivel de las dos y sus motivos (sin repetir)."""
    out = []
    for i in range(len(filas) - 1):
        a, b = filas[i], filas[i + 1]
        lv = max(a["nivel"], b["nivel"])
        motivos = list(dict.fromkeys(a["motivos"] + b["motivos"]))
        out.append({"n": i + 1, "desde_km": a["d_km"], "hasta_km": b["d_km"], "desde": a.get("t"), "hasta": b.get("t"), "nivel": lv, "color": COLOR[lv],
                    "texto": TEXTO[lv], "motivos": motivos, **({"cota_m": [a.get("ele"), b.get("ele")]} if a.get("ele") is not None else {})})
    return out


def _resumen(perfil: str, tramos: list[dict], ventanas: list[dict]) -> dict:
    lv = max((t["nivel"] for t in tramos), default=0)
    mejor = min(ventanas, key=lambda w: (w["nivel"], w["puntuacion"], w["salida"])) if ventanas else None
    criticos = [t for t in tramos if t["nivel"] == lv and lv > 0]
    return {"nivel": lv, "color": COLOR[lv], "texto": TEXTO[lv],
            "tramos_criticos": [f"km {t['desde_km']:g}–{t['hasta_km']:g}: {', '.join(t['motivos'][:2])}" for t in criticos[:3]],
            "mejor_ventana": mejor}


def analizar(res: dict, perfil: str) -> dict:
    """Semáforo por tramos, mejor ventana y equipo a partir del resultado de routing.plan (monte o carretera). Sin consultas externas."""
    filas = []
    for s in res.get("muestras", []):
        if s.get("nivel") is None:
            continue
        t = datetime.fromisoformat(s["t"]) if s.get("t") else None
        de_noche = t is not None and not routing.is_daylight(t, s["lat"], s["lon"])
        lv2, why2 = reglas_extra(perfil, s["meteo"], s.get("ele"), _hora_local(s.get("t"), int(s["lon"] * 240)), de_noche)      # hora solar del punto
        filas.append({**s, "nivel": max(s["nivel"], lv2), "motivos": list(dict.fromkeys(s["motivos"] + why2)), "de_noche": de_noche})
    if len(filas) < 2:
        return {"perfil": perfil, "disponible": False, "motivo": "La hora de salida queda fuera de los datos (máx. 72 h)"}
    tramos = _tramos(filas)
    return {"perfil": perfil, "disponible": True, **_resumen(perfil, tramos, res.get("ventanas") or []), "tramos": tramos,
            "equipo": _equipo(perfil, filas, {"repetidores": len(res.get("repetidores") or [])}),
            "nota": "Semáforo por reglas (no IA) sobre la media de modelos a la hora de paso. No sustituye a los avisos oficiales ni a tu criterio."}


# ---------- mar ----------
def _nivel_mar(m: dict, de_noche: bool) -> tuple[int, list[str]]:
    lv, why = 0, []

    def sube(n, txt):
        nonlocal lv
        lv = max(lv, n)
        why.append(txt)
    ola, viento, racha = m.get("ola_m"), m.get("viento_kmh") or 0, m.get("racha_kmh") or 0
    if ola is not None:
        if ola >= 2.5:
            sube(2, f"Oleaje de {ola:.1f} m ({mar_mod.estado(ola)[0]})")
        elif ola >= 1.5:
            sube(1, f"Oleaje de {ola:.1f} m ({mar_mod.estado(ola)[0]})")
    if viento >= 50:
        sube(2, f"Viento de {round(viento / NUDO_KMH)} nudos (fuerza 6 o más)")
    elif viento >= 37:
        sube(1, f"Viento de {round(viento / NUDO_KMH)} nudos")
    if racha >= 65:
        sube(2, f"Rachas de {round(racha / NUDO_KMH)} nudos")
    elif racha >= 50:
        sube(1, f"Rachas de {round(racha / NUDO_KMH)} nudos")
    if (m.get("cape_jkg") or 0) >= 800 and (m.get("lluvia_mm") or 0) >= 0.3:
        sube(2, "Riesgo de tormenta")
    if (m.get("lluvia_mm") or 0) >= 4:
        sube(1, "Chubascos fuertes (visibilidad)")
    if de_noche:
        sube(1, "Navegación de noche")
    return lv, why


def _ola_a(mar: dict | None, t: datetime, offset_s: int) -> float | None:
    if not mar or not mar.get("hay_mar"):
        return None
    loc = (t.astimezone(timezone.utc) + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:00")
    return next((h["ola_m"] for h in mar["horas"] if h["hora"] == loc), None)


async def plan_mar(http: httpx.AsyncClient, waypoints: list[tuple[float, float]], depart: datetime | None, nudos: float = 5.0,
                   ahora: datetime | None = None) -> dict:
    """Travesía en línea recta entre los puntos: hasta 8 muestras, oleaje y viento a la hora de paso, ventanas en las próximas 36 h."""
    ahora = ahora or datetime.now(timezone.utc)
    coords = list(waypoints)
    cum = routing.cumulative_km(coords)
    total = cum[-1]
    if total < 0.2:
        raise ValueError("La travesía es demasiado corta")
    n = max(3, min(8, round(total / 5) + 1))
    vel = nudos * NUDO_KMH
    muestras = []
    for i in range(n):
        d = total * i / (n - 1)
        la, lo = routing.point_at(coords, cum, d)
        muestras.append({"lat": round(la, 5), "lon": round(lo, 5), "d_km": round(d, 2), "eta_min": round(d / vel * 60, 1)})
    raw = await openmeteo.fetch_many([(m["lat"], m["lon"]) for m in muestras], days=3, client=http)
    horas = [openmeteo.hourly_consensus(r, ahora) for r in raw]
    mares = []
    for m in muestras:                               # caché de 1 h por celda de ~10 km: puntos cercanos comparten consulta
        try:
            mares.append(await mar_mod.fetch(http, m["lat"], m["lon"]))
        except httpx.HTTPError:
            mares.append(None)
    off = raw[0].get("utc_offset_seconds", 0) if raw else 0
    tiempos = [routing._utc_times(h) for h in horas]

    def evaluar(salida: datetime) -> list[dict] | None:
        filas = []
        for m, h, ut, mr in zip(muestras, horas, tiempos, mares):
            t = salida + timedelta(minutes=m["eta_min"])
            w = routing.at(h, t, ut)
            if w is None:
                return None
            met = {"temp_c": routing._r(w["temp"]), "viento_kmh": routing._r(w["wind"], 3.6), "racha_kmh": routing._r(w["gust"], 3.6), "viento_dir": w["dir"],
                   "lluvia_mm": routing._r(w["rain"], 1, 1), "cape_jkg": routing._r(w["cape"]), "ola_m": _ola_a(mr, t, off)}
            noche = not routing.is_daylight(t, m["lat"], m["lon"])
            lv, why = _nivel_mar(met, noche)
            filas.append({**m, "t": t.isoformat(), "nivel": lv, "motivos": why, "meteo": met, "de_noche": noche})
        return filas
    salida = depart or (ahora + timedelta(minutes=10))
    filas = evaluar(salida)
    ventanas = []
    inicio = (ahora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    for k in range(36):
        f = evaluar(inicio + timedelta(hours=k))
        if f:
            ventanas.append({"salida": (inicio + timedelta(hours=k)).isoformat(), "nivel": max(x["nivel"] for x in f),
                             "puntuacion": round(sum(x["nivel"] ** 2 + 0.1 * x["nivel"] for x in f), 2), "motivos": sorted({w for x in f for w in x["motivos"]})})
    ventanas.sort(key=lambda c: (c["puntuacion"], c["salida"]))
    elegidas: list[dict] = []
    for c in ventanas:
        if all(abs((datetime.fromisoformat(c["salida"]) - datetime.fromisoformat(x["salida"])).total_seconds()) >= 7200 for x in elegidas):
            elegidas.append(c)
        if len(elegidas) == 3:
            break
    elegidas.sort(key=lambda c: c["salida"])
    base = {"perfil": "mar", "distancia_km": round(total, 1), "distancia_mn": round(total / NUDO_KMH, 1), "duracion_min": round(total / vel * 60),
            "velocidad_nudos": nudos, "salida": salida.isoformat(), "ventanas": elegidas, "sin_datos_de_oleaje": not any(m and m.get("hay_mar") for m in mares)}
    if not filas:
        return {**base, "disponible": False, "motivo": "La hora de salida queda fuera de los datos (máx. 72 h)"}
    tramos = _tramos(filas)
    return {**base, "disponible": True, **_resumen("mar", tramos, elegidas), "tramos": tramos, "muestras": filas, "equipo": _equipo("mar", filas, {}),
            "nota": "Tramos en línea recta a velocidad constante; oleaje del modelo marino (malla de 5-25 km: cerca de la costa puede diferir). Semáforo por reglas (no IA). "
                    "No sustituye al parte de AEMET ni de Salvamento Marítimo."}

