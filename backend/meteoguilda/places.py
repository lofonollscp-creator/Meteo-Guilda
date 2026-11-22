"""Lugares foco del proyecto.

Coordenadas verificadas el 2026-11-22 contrastando al menos dos fuentes independientes
(geocodificador de Open-Meteo y Nominatim/OpenStreetMap; Bresca además con Wikipedia y el
registro del patrimonio de la parroquia de Sant Miquel). Open-Meteo aplica su propia elevación DEM.
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
    Place("garos", "Garòs", "Naut Aran · Val d'Aran", "Catalunya", 42.7000, 0.8470,
          "montaña", METEOCAT, "Meteocat"),
    Place("premia-de-mar", "Premià de Mar", "Maresme", "Catalunya", 41.4922, 2.3630,
          "costa", METEOCAT, "Meteocat"),
    Place("tortoles-de-esgueva", "Tórtoles de Esgueva", "Burgos", "Castilla y León", 41.8162, -4.0230,
          "llanura", AEMET, "AEMET"),
    Place("bresca", "Bresca", "Baix Pallars · Pallars Sobirà", "Catalunya", 42.3114, 1.0683,
          "montaña", METEOCAT, "Meteocat"),
    Place("la-garrovilla", "La Garrovilla", "Badajoz", "Extremadura", 38.9200, -6.4770,
          "llanura", AEMET, "AEMET"),
    Place("bagneres-de-bigorre", "Bagnères-de-Bigorre", "Hautes-Pyrénées", "Occitanie", 43.0654, 0.1509,
          "montaña", METEOFRANCE, "Météo-France Vigilance"),
]
BY_SLUG = {p.slug: p for p in PLACES}
