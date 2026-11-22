# Meteo Guilda — Plan de proyecto

> Previsión meteorológica "sin cocinar": datos crudos (balizas APRS/CWOP, modelos numéricos abiertos, boyas, METAR…) sintetizados por IA (OpenRouter, modelos gratuitos) y cruzados con planes de viaje: monte, carretera, vuelos, barcos, surf, etc.

---

## 1. Visión y principios

1. **Datos primarios, no pronósticos de terceros.** Entran observaciones y salidas de modelos numéricos abiertos; no se consumen previsiones ya redactadas por Google, eltiempo.es, Yr, etc.
2. **La IA interpreta, no inventa.** Todos los números (temperatura, viento, altura de ola…) salen de los datos y se pasan tal cual; el LLM solo razona, resume y recomienda, con salida JSON validada.
3. **Transparencia.** Cada pronóstico enseña de qué fuentes sale, el grado de acuerdo entre modelos (confianza) y los datos crudos desplegables.
4. **Cruce con el viaje.** La previsión se evalúa *a lo largo de una ruta y un horario*, no solo en un punto.
5. **Seguridad primero.** Los avisos oficiales (AEMET/Meteoalerta, Salvamento Marítimo…) se muestran siempre aparte y destacados. La IA nunca contradice un aviso; es un apoyo, no un servicio de emergencia.

> Matiz honesto: "sin cocinar" aplica a los *datos*. La capa de IA es, por definición, un pronóstico elaborado, así que hay que hacerla auditable (punto 3). Los avisos oficiales se usan solo como capa de seguridad, no como fuente del pronóstico.

---

## 2. Fuentes de datos

### 2.1 Observaciones en tiempo real
| Fuente | Qué aporta | Acceso | Notas |
|---|---|---|---|
| **APRS-IS** (balizas meteo + CWOP) | Temp, humedad, presión, viento/racha, lluvia, radiación | Socket TCP a `rotate.aprs2.net:14580`, filtro `t/w` + área (`a/` o `r/lat/lon/km`) | Solo lectura: passcode `-1`, sin necesidad de indicativo. Formato APRS101 cap. 12 (temp en °F, viento mph, lluvia centésimas de pulgada → convertir a SI). Calidad desigual → control de calidad obligatorio |
| **METAR/SPECI/TAF** (aviationweather.gov, NOAA) | Aeropuertos: viento, visibilidad, techo de nubes | API pública | Clave para vuelos |
| **SYNOP / NOAA ISD** | Estaciones sinópticas | NOAA / OGIMET | Complemento |
| **AEMET OpenData – observaciones** | Red oficial de estaciones | API key gratuita | Solo *observaciones*, no sus predicciones |
| **Redes autonómicas abiertas** (MeteoGalicia, Meteocat, Euskalmet, etc.) | Estaciones densas regionales | Open data de cada una | Priorizar según región objetivo |
| **Boyas** (Puertos del Estado, NDBC, CMEMS) | Oleaje real: Hs, Tp, dirección, temp. del mar | Datos abiertos | Verdad-terreno para surf y navegación |
| **Rayos** (Blitzortung) | Descargas en tiempo real | Comunidad; **revisar licencia** (uso restringido) | Opcional |
| **Radar/satélite** (OPERA/EUMETNET, EUMETSAT) | Precipitación y nubes | Abiertos, requieren procesado | Fase avanzada |

### 2.2 Modelos numéricos (previsión cruda)
| Modelo | Alcance | Acceso |
|---|---|---|
| **ECMWF IFS (open data)** | Global 0.25°, CC-BY-4.0 | GRIB en data.ecmwf.int / AWS |
| **NOAA GFS** | Global 0.25° | NOAA AWS Open Data / NOMADS |
| **DWD ICON / ICON-EU** | Europa ~7 km, global 13 km | opendata.dwd.de |
| **Météo-France AROME/ARPEGE** | Alta resolución (1.3 km) | Portal Météo-France (licencia abierta, clave) |
| **Oleaje**: NOAA WaveWatch III, ECMWF WAM, MFWAM | Altura, periodo, dirección | Vía GRIB o Open-Meteo Marine |
| **Atmósfera/calidad aire**: CAMS (Copernicus) | Polvo del Sáhara, ozono… | Copernicus ADS (gratis) |
| **Incendios**: EFFIS, NASA FIRMS | Riesgo y focos | Abiertos |

**Estrategia práctica:** empezar con **Open-Meteo** (API sobre modelos abiertos, sin clave, datos crudos por modelo: `models=ecmwf_ifs025,icon_eu,gfs_seamless,meteofrance_arome…`) para llegar rápido a un MVP, y migrar progresivamente a **ingesta propia de GRIB** (xarray/cfgrib) o a un Open-Meteo autoalojado. ⚠️ El tier gratuito de Open-Meteo es solo no comercial y con límites de llamadas: revisar términos si el proyecto monetiza.

### 2.3 Fuentes para el cruce de viajes
| Módulo | Fuentes |
|---|---|
| **Monte/senderismo** | OpenStreetMap + Overpass (cimas, refugios, senderos), modelos de elevación Copernicus DEM / SRTM, boletines de aludes (AEMET/Météo-France BRA), isocero y nieve de modelos |
| **Carretera** | Enrutado: OSRM / Valhalla / OpenRouteService (autoalojable). Incidencias y cámaras: **DGT DATEX II** (abierto). Se muestrea la ruta cada N km/minutos y se cruza con la previsión a la hora estimada de paso |
| **Vuelos** | METAR/TAF origen/destino/alternativos, **OpenSky Network** (ADS-B, posiciones), vientos en altura de GFS/IFS (turbulencia, viento en cara), horarios: AeroDataBox/AviationStack (límites gratuitos) |
| **Barcos/ferris** | AIS: **aisstream.io** (websocket gratuito) / AISHub; oleaje y viento en la ruta; boyas |
| **Surf** | Base de spots (OSM + curada a mano: orientación, fondo, mareas ideales), oleaje de modelos + boyas, viento local (balizas APRS cercanas a la playa), mareas (Puertos del Estado / cálculo armónico) |
| **Extras** | Vela y kitesurf, pesca (presión/marea/luna), parapente (térmicas, viento en altura), astrofotografía (nubosidad + Luna con `astral`/`skyfield`), ciclismo, incendios, calidad de aire/polvo, esquí (isocero, nieve) |

---

## 3. Arquitectura

```
 APRS-IS ─┐                                     ┌──────────────┐
 METAR ───┤  Ingestores (workers)   ┌─────────► │ API (FastAPI)│ ◄──► Web (Next.js + MapLibre)
 Boyas ───┼► normalizar + QC ──► DB │           └──────┬───────┘
 GRIB/API ┘  (Python, cron/stream)  │                  │ herramientas (tool-calling)
                                     ▼                  ▼
                       PostgreSQL + PostGIS + TimescaleDB   Motor de IA ──► OpenRouter (:free, con fallback)
                       Redis (caché/colas)  ◄─ Pronósticos pre-generados ◄─┘
                       Object storage (GRIB/tiles)
```

- **Backend/ingesta:** Python 3.12 (`aprslib`, `xarray`, `cfgrib`, `httpx`), FastAPI, workers con APScheduler/Celery o `arq`.
- **Base de datos:** PostgreSQL con PostGIS (estaciones, rutas, spots) y TimescaleDB (series temporales de observaciones y rejillas muestreadas).
- **Frontend:** Next.js + TypeScript, MapLibre GL (mapas OSM vector), gráficos con ECharts/Plot. PWA instalable, mobile-first (el uso real es en el móvil, a pie de monte).
- **Infra:** Docker Compose al inicio; un VPS pequeño (Hetzner/OVH) basta para el MVP. CI con GitHub Actions.

### 3.1 Pipeline de calidad de datos APRS (crítico)
1. Parseo y conversión de unidades a SI.
2. Descartar paquetes fuera de rango físico (p. ej. T < −60 °C, viento > 80 m/s, presión incoherente con la altitud).
3. **Consistencia espacial:** comparar con vecinas en radio de ~25 km y con el modelo (z-score); marcar valores atípicos.
4. **Reputación de estación:** historial de fiabilidad, lista negra automática (sensores al sol, bloqueados, sin actualizar).
5. Corrección de presión a nivel del mar con altitud (DEM).
6. Resultado: cada observación con `quality_score`; solo las ≥ umbral alimentan a la IA.

Uso de las observaciones: **nowcasting** (0–6 h) y **verificación/sesgo local** de los modelos (si el modelo sobreestima el viento en un valle, la IA lo sabe con evidencia).

---

## 4. Motor de IA (OpenRouter, modelos gratuitos)

### 4.1 Realidades de los modelos `:free`
- Límites bajos (del orden de decenas de peticiones/minuto y pocas decenas/día sin saldo; más con un saldo mínimo cargado — **verificar cifras vigentes en la documentación de OpenRouter**), modelos que aparecen y desaparecen, y los proveedores gratuitos pueden registrar prompts. **No enviar datos personales.**
- Por tanto: no se puede llamar a la IA por cada visita.

### 4.2 Diseño
1. **Catálogo dinámico:** consultar `GET /api/v1/models`, filtrar `pricing.prompt == 0`, ordenar por contexto/capacidad y mantener una lista de fallback configurable (o usar el router `openrouter/free`). Reintentos con el siguiente modelo si hay 429/5xx/JSON inválido.
2. **Pronósticos pre-generados + caché:** un job genera el pronóstico por zona/comarca y por "perfil" (general, monte, mar, surf…) cada 3–6 h y tras cada nueva pasada de modelo. El usuario lee de caché → coste cero y respuesta inmediata.
3. **Consultas libres bajo demanda** (p. ej. "¿puedo subir al Aneto el sábado?") con *tool-calling*: la IA llama a herramientas propias (`get_forecast(lat,lon,t)`, `get_observations(bbox)`, `route_weather(ruta,salida)`, `wave_forecast(spot)`, `notams/metar`) en lugar de recibir todo en el prompt. Cuota por usuario y cola.
4. **Salida estructurada:** JSON con esquema (resumen, riesgos, ventanas óptimas, confianza, recomendaciones, `sources_used`). Validar con Pydantic; si falla, reintentar o degradar a una plantilla determinista.
5. **Anti-alucinación:**
   - Cálculos (acuerdo entre modelos, sensación térmica, índice de riesgo, ventanas) hechos en **código**, no por el LLM.
   - Pasar a la IA datos ya resumidos (media, rango entre modelos, tendencia, sesgo) para ahorrar tokens.
   - Post-validación: toda cifra citada debe existir en el contexto; si no, se rechaza.
   - Prompt en español, con instrucciones de expresar incertidumbre y prohibido inventar.
6. **Confianza:** dispersión entre ECMWF/ICON/GFS/AROME (+ ensemble si hay) → "alta/media/baja". Si los modelos discrepan, la web lo dice.
7. **Degradación elegante:** si todos los modelos gratuitos fallan, se muestran los datos y gráficos crudos con un resumen por reglas.
8. Evaluación continua: guardar cada pronóstico y compararlo con las observaciones reales (verificación) → métricas públicas de acierto.

---

## 5. Funcionalidades (producto)

**Núcleo**
- Buscador/mapa: pronóstico de IA para cualquier punto, con datos crudos desplegables y estaciones APRS cercanas en vivo.
- Mapa de estaciones con calidad y capas de viento/lluvia/olas.
- Avisos oficiales destacados.

**Planificador de salidas (diferenciador)**
- Entrada: ruta (GPX, dibujada o origen-destino), modo (a pie, coche, vuelo, barco, surf…), fecha/hora.
- Salida: línea de tiempo por tramos con meteo a la hora de paso, **semáforo de riesgo**, mejor ventana horaria, equipamiento sugerido y plan B.
- Ejemplos: "Salida a Peña Ubiña sábado" → viento/racha en cresta, isocero, visibilidad, hora límite de descenso por tormentas. "Madrid→Santander en coche" → niebla/hielo/lluvia fuerte por tramo e incidencias DGT. "Vuelo a Tenerife" → METAR/TAF, viento en cara, turbulencia. "Ferry Algeciras–Ceuta" → oleaje y viento en el Estrecho.

**Surf**: ranking de spots por tamaño/periodo/viento (offshore/onshore)/marea, mejor hora, comparación con boyas reales.

**Cuenta de usuario (opcional, fase posterior):** lugares/rutas guardadas, alertas push/Telegram ("viento >60 km/h en tu ruta").

---

## 6. Hoja de ruta

| Fase | Duración orient. | Entregables |
|---|---|---|
| **0 – Cimientos** | 1 sem | Repo, Docker Compose, CI, esquema BD, decisiones de alcance geográfico |
| **1 – Datos** | 2–3 sem | Ingestor APRS-IS + QC, ingesta Open-Meteo multi-modelo, METAR, boyas; API de lectura; mapa de estaciones |
| **2 – IA MVP** | 2 sem | Cliente OpenRouter con fallback, pronóstico por punto/zona, JSON validado, caché, página de pronóstico con datos crudos |
| **3 – Viajes v1** | 3 sem | Planificador de rutas a pie y coche (GPX + OSRM), meteo por tramos, semáforo de riesgo, DGT |
| **4 – Mar y aire** | 3 sem | Surf (spots + boyas + mareas), navegación/ferris, vuelos (METAR/TAF + viento en altura) |
| **5 – Calidad y producto** | continuo | Verificación contra observaciones, métricas de acierto, alertas, cuentas, PWA offline, i18n |
| **6 – Autonomía de datos** | opcional | Ingesta propia de GRIB (ECMWF/ICON/AROME), autoalojar Open-Meteo, post-procesado estadístico con sesgo local |

**MVP recomendado (≈6–7 semanas):** fases 0–3 limitadas a **una región piloto** (p. ej. Cordillera Cantábrica o la que elijas) para tener densidad de balizas y validar la calidad.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Datos APRS ruidosos o escasos en algunas zonas | QC espacial + modelo; el sistema funciona sin balizas (solo modelos) y las balizas mejoran el nowcasting |
| Cuotas/caídas de modelos gratuitos de IA | Caché, pre-generación, fallback multi-modelo, degradación por reglas; opción de pasar a modelo de pago barato sin cambiar la arquitectura |
| Alucinaciones con riesgo de seguridad (montaña/mar) | Cálculos en código, validación de cifras, avisos oficiales siempre visibles, descargo de responsabilidad, tono de incertidumbre |
| Licencias (Open-Meteo no comercial, Blitzortung, aprs.fi, DATEX) | Registro de licencias por fuente; atribución; preferir APRS-IS directo a aprs.fi; Open-Meteo autoalojado si se monetiza |
| Resolución de modelos globales en terreno complejo (valles, costa) | Usar AROME/ICON-EU; corrección de sesgo con estaciones locales; mostrar confianza baja |
| Responsabilidad legal | Aviso claro: no sustituye a servicios oficiales; no uso para decisiones críticas sin contraste |
| Privacidad | No enviar datos de usuario al LLM; rutas anonimizadas en prompts |

---

## 8. Costes estimados (MVP)
- VPS 4 vCPU / 8 GB: ~10–20 €/mes. Dominio: ~10 €/año.
- OpenRouter: 0 € con modelos `:free` (cuota limitada); cargar unos pocos € de saldo suele subir los límites diarios.
- Datos: 0 € (todo abierto; claves gratuitas de AEMET, Météo-France, etc.).

---

## 9. Decisiones pendientes (necesito tu input)
1. **Región piloto**: ¿España entera, una comunidad, o Europa?
2. **Prioridad de módulos** para el MVP: ¿monte, carretera, surf, vuelos, mar?
3. **Stack**: ¿te encaja Python (FastAPI) + Next.js, o prefieres todo TypeScript?
4. **Hosting** y si hay intención de **monetizar** (afecta a licencias, p. ej. Open-Meteo).
5. **¿Tienes indicativo de radioaficionado?** Para leer APRS no hace falta, pero sí si más adelante quieres que tu propia estación emita/colabore a la red.
6. **Idioma**: ¿solo español o multi-idioma desde el inicio?

---

## 10. Primeros pasos concretos (siguiente sesión)
1. Inicializar monorepo: `/backend` (FastAPI), `/ingest` (workers), `/web` (Next.js), `docker-compose.yml`.
2. Ingestor APRS-IS con filtro `t/w` sobre la región piloto, parser de paquetes meteo y tests con paquetes reales.
3. Cliente Open-Meteo multi-modelo y tabla de series temporales.
4. Prototipo del prompt + esquema JSON del pronóstico, probado contra 2–3 modelos `:free`.
5. Mapa MapLibre con estaciones y primer pronóstico de IA por punto.
