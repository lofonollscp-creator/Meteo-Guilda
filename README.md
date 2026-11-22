# Meteo Guilda

Previsión meteorológica a partir de datos abiertos (ECMWF, ICON, GFS, Météo-France vía Open-Meteo) y balizas meteo
APRS, con lectura de IA (OpenRouter, modelos gratuitos), **planificador de rutas** con la meteo a la hora de paso,
**espacio para radioaficionados** y un **servidor MCP** para conectar otras IAs.
Plan completo: [PLAN.md](PLAN.md) · [despliegue en VPS](docs/DEPLOY.md) · [decisiones técnicas](docs/decisiones.md).

## Qué incluye (v0.2)
- **Previsión**: 6 lugares principales (Garòs, Premià de Mar, Tórtoles de Esgueva, Bresca, La Garrovilla,
  Bagnères-de-Bigorre), búsqueda libre de cualquier lugar, favoritos y anclados, meteograma, gráficos (temperatura con
  banda entre modelos, lluvia, nieve, viento con flechas, nubes, presión, isocero) y balizas APRS cercanas.
- **Rutas** (a pie / en coche, o GPX propio): tiempo en cada punto *a la hora de paso*, semáforo de riesgo por reglas,
  perfil de elevación coloreado, mejores ventanas de salida, balizas y repetidores junto al recorrido, exportación GPX.
- **Radio**: repetidores con cobertura estimada (línea de vista) en PostGIS, propagación troposférica VHF/UHF (gradiente
  de refractividad), condiciones HF (NOAA SWPC), cuaderno QSL con exportación ADIF, locator/código Q/fonético/CTCSS.
- **Aprende**: guías de meteorología, radioafición y su relación.
- **MCP**: `list_places`, `search_place`, `get_forecast_data`, `get_ai_forecast`, `plan_route`, `get_nearby_observations`,
  `get_vhf_propagation`, `get_hf_conditions`, `find_repeaters`, `maidenhead`.

## Desarrollo
```bash
# PostgreSQL con PostGIS (p. ej. con Docker)
docker run -d --name mg-db -e POSTGRES_USER=mg -e POSTGRES_PASSWORD=mg -e POSTGRES_DB=meteoguilda_test -p 5432:5432 postgis/postgis:16-3.4
cd backend && pip install -e '.[dev]'
MG_DATABASE_URL=postgresql://mg:mg@127.0.0.1:5432/meteoguilda_test python -m pytest   # los tests de BD se omiten si no hay PostGIS
```
Producción: `docker compose up -d --build` (ver docs/DEPLOY.md).
