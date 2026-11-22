# Meteo Guilda

Previsión meteorológica a partir de datos abiertos (modelos ECMWF/ICON/GFS/Météo-France vía Open-Meteo) y
balizas meteo APRS, sintetizada por IA (OpenRouter, modelos gratuitos) con validación anti-alucinación.
Plan completo: [PLAN.md](PLAN.md).

## Estado (v0.1)
- Parser APRS meteo + control de calidad (rango y vecinos) + ingestor APRS-IS (`backend/meteoguilda/ingest.py`)
- Resumen determinista multi-modelo con confianza (`openmeteo.py`)
- IA con fallback entre modelos `:free`, JSON validado y comprobación de cifras (`ai.py`); resumen por reglas si falla
- API FastAPI (`/api/forecast`, `/api/stations`) y web con mapa (`web/index.html`)
- Pendiente: planificador de rutas/viajes, surf, vuelos, mar (fases 3–4 del plan)

## Arranque local
```bash
cd backend && pip install -e '.[dev]' && python -m pytest
cp ../.env.example ../.env   # añade OPENROUTER_API_KEY
python -m meteoguilda.ingest &            # recibe balizas APRS-IS
uvicorn meteoguilda.main:app --reload     # http://localhost:8000
```
O con Docker: `docker compose up --build`.
