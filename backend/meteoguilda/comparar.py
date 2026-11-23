"""Comparación de 2-4 lugares con los mismos modelos: cifras de las próximas 48 h, series para superponer y un veredicto en palabras."""
from __future__ import annotations

N = 16          # tramos de 3 h = 48 h
MS_KMH = 3.6


def _m(step: dict, var: str, key: str = "mean"):
    v = (step.get(var) or {}).get(key)
    return None if v is None else v


def fila(nombre: str, lat: float, lon: float, summary: dict) -> dict:
    steps = summary["steps"][:N]
    temp = [_m(s, "temperature_2m") for s in steps]
    tmin = [_m(s, "temperature_2m", "min") for s in steps]
    tmax = [_m(s, "temperature_2m", "max") for s in steps]
    lluvia = [round(_m(s, "precipitation") or 0, 2) for s in steps]
    racha = [None if _m(s, "wind_gusts_10m", "max") is None else round(_m(s, "wind_gusts_10m", "max") * MS_KMH) for s in steps]
    viento = [None if _m(s, "wind_speed_10m") is None else round(_m(s, "wind_speed_10m") * MS_KMH) for s in steps]
    nubes = [_m(s, "cloud_cover") for s in steps]
    ok = lambda a: [x for x in a if x is not None]  # noqa: E731
    return {"nombre": nombre, "lat": lat, "lon": lon, "elevacion_m": summary.get("elevation_m"),
            "ahora_c": temp[0] if temp else None, "min_c": min(ok(tmin)) if ok(tmin) else None, "max_c": max(ok(tmax)) if ok(tmax) else None,
            "lluvia_48h_mm": round(sum(lluvia), 1), "racha_max_kmh": max(ok(racha)) if ok(racha) else None,
            "nubes_media_pct": round(sum(ok(nubes)) / len(ok(nubes))) if ok(nubes) else None,
            "tiempo": [s["time"] for s in steps],
            "series": {"temp_c": temp, "lluvia_mm": lluvia, "racha_kmh": racha, "viento_kmh": viento, "nubes_pct": nubes}}


def veredicto(filas: list[dict]) -> list[str]:
    """Frases comparativas; solo se afirma una diferencia cuando es apreciable (no se inventa un ganador en empates)."""
    out: list[str] = []
    if len(filas) < 2:
        return out

    def extremo(clave, mayor=True, minimo=0.0):
        v = [(f[clave], f["nombre"]) for f in filas if f.get(clave) is not None]
        if len(v) < 2:
            return None
        v.sort(key=lambda t: t[0], reverse=mayor)
        return v[0] if abs(v[0][0] - v[-1][0]) >= minimo else None

    a = extremo("ahora_c", True, 2)
    if a:
        b = extremo("ahora_c", False)
        out.append(f"Ahora hace más calor en {a[1]} ({a[0]:.0f} °C) y más frío en {b[1]} ({b[0]:.0f} °C).")
    l = extremo("lluvia_48h_mm", True, 1)
    if l and l[0] > 0.5:
        out.append(f"{l[1]} acumula más lluvia en 48 h ({l[0]:.1f} mm).")
    elif all(f["lluvia_48h_mm"] < 0.5 for f in filas):
        out.append("No se espera lluvia apreciable en ninguno.")
    r = extremo("racha_max_kmh", True, 10)
    if r:
        out.append(f"Más viento en {r[1]}: rachas de hasta {r[0]:.0f} km/h.")
    # «mejor» = menos lluvia y menos racha; solo si hay una diferencia clara en alguno de los dos
    punt = sorted(((f["lluvia_48h_mm"] * 2 + (f["racha_max_kmh"] or 0) / 10, f["nombre"]) for f in filas))
    if punt[-1][0] - punt[0][0] >= 3:
        out.append(f"Para estar al aire libre, el mejor de los comparados en 48 h es {punt[0][1]} (menos lluvia y viento).")
    return [x for x in out if x]
