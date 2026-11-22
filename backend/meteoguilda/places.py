"""Lugares foco del proyecto.

⚠️ Coordenadas aproximadas (±2–3 km): verificar con el mapa y corregir aquí.
Open-Meteo aplica su propia elevación DEM al punto.
"""
from __future__ import annotations

from dataclasses import dataclass

AEMET = "https://www.aemet.es/es/eltiempo/prediccion/avisos"
METEOCAT = "https://www.meteo.cat/prediccio/avisos"
METEOFRANCE = "https://vigilance.meteofrance.fr/fr"


@dataclass(frozen=True)
class Place:
    slug: str
    name: str
    area: str        # comarca / provincia / departamento
    region: str
    lat: float
    lon: float
    kind: str        # montaña | costa | llanura
    warnings_url: str
    warnings_label: str


PLACES: list[Place] = [
    Place("garos", "Garòs", "Naut Aran · Val d'Aran", "Catalunya", 42.7100, 0.8600,
          "montaña", METEOCAT, "Meteocat"),
    Place("premia-de-mar", "Premià de Mar", "Maresme", "Catalunya", 41.4903, 2.3637,
          "costa", METEOCAT, "Meteocat"),
    Place("tortoles-de-esgueva", "Tórtoles de Esgueva", "Burgos", "Castilla y León", 41.9300, -3.9700,
          "llanura", AEMET, "AEMET"),
    Place("bresca", "Bresca", "Baix Pallars · Pallars Sobirà", "Catalunya", 42.3360, 1.0800,
          "montaña", METEOCAT, "Meteocat"),
    Place("la-garrovilla", "La Garrovilla", "Badajoz", "Extremadura", 38.9500, -6.4750,
          "llanura", AEMET, "AEMET"),
    Place("bagneres-de-bigorre", "Bagnères-de-Bigorre", "Hautes-Pyrénées", "Occitanie", 43.0647, 0.1494,
          "montaña", METEOFRANCE, "Météo-France Vigilance"),
]
BY_SLUG = {p.slug: p for p in PLACES}
