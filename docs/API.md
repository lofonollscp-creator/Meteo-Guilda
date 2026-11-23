# API de Meteo Guilda · Radio Guilda

Esquema OpenAPI completo: `/api/openapi.json`. Respuestas en JSON. Los límites por IP devuelven `429`.
Las mismas funciones están disponibles como herramientas del [servidor MCP](../backend/meteoguilda/mcp_server.py); la correspondencia completa está en [MAPA-FUNCIONES.md](MAPA-FUNCIONES.md).
Toda la API exige la sesión de Guilda Work (cookie) salvo `/api/health`, los metadatos OAuth y `/mcp` (token OAuth).

## Meteo
| Método y ruta | Descripción |
|---|---|
| `GET /api/places` | Los 6 lugares principales con instantánea |
| `GET /api/search?q=` | Buscar cualquier lugar (coordenadas y altitud) |
| `GET /api/forecast?place=slug` o `?lat=&lon=&name=` (`&refresh=1`) | Datos de 8 modelos (`por_modelo`), observaciones y resumen por reglas, **al instante** (sin esperar a la IA) |
| `GET /api/forecast/ai?place=` o `?lat=&lon=` (`&solo_cache=1`) | Lectura redactada por la IA de tu cuenta. **Solo se genera si se pide** (gasta tokens); con `solo_cache=1` devuelve la vigente o `{"origen":"pendiente"}` |
| `GET /api/activities` · `GET /api/activity?activity=&lat=&lon=&min_score=` | Actividades y sus mejores ventanas (puntuación 0-100 por tramos de 3 h) |
| `GET /api/air?lat=&lon=` | Calidad del aire (EAQI), UV y polen: ahora, máximo de hoy y 24 h |
| `GET /api/reverse?lat=&lon=` | Nombre del sitio (municipio, comarca, región) y locator |
| `GET /api/raw?lat=&lon=` | Datos crudos de modelos y balizas, sin texto de IA |
| `GET /api/snapshot?pts=lat,lon;lat,lon` | Instantánea de hasta 15 puntos |
| `GET /api/stations?south=&west=&north=&east=` | Estaciones APRS con calidad |
| `GET /api/observations?lat=&lon=&km=` | Estaciones cercanas a un punto |
| `POST /api/route` | Ruta (a pie/coche/GPX) con meteo a la hora de paso, riesgo y ventanas |
| `GET /api/astro?lat=&lon=` | Sol (orto, ocaso, crepúsculos) y Luna |

## Radio
| Método y ruta | Descripción |
|---|---|
| `GET /api/repeaters?bbox=&band=&mode=&q=&limit=` | Repetidores (FM, DMR, D-STAR, C4FM) |
| `GET /api/repeaters/near?lat=&lon=&km=&mode=&band=` | Repetidores cercanos y si cubren el punto |
| `GET /api/repeaters/{id}` · `POST /api/repeaters/{id}/coverage` | Ficha y cálculo de cobertura estimada |
| `GET /api/repeaters/stats` | Resumen del catálogo |
| `GET /api/propagation?lat=&lon=` | Propagación troposférica 72 h |
| `GET /api/solar` | Kp y flujo solar (NOAA) |
| `GET /api/satellites` · `GET /api/satellites/passes?lat=&lon=&hours=&min_el=&sats=` | Satélites y sus pasos |
| `GET /api/portable?lat=&lon=` | Mejores ventanas para operar en portátil |
| `GET /api/avisos?lat=&lon=` | Avisos oficiales vigentes (AEMET) del punto + enlaces; requiere `AEMET_API_KEY` para los avisos |
| `GET /api/radio/ventanas?lat=&lon=&modo=fijo\|portatil` | Tramos de 3 h (48 h) con nota de HF, tropo, satélites y campo |
| `GET /api/locator?lat=&lon=` o `?loc=` | Maidenhead ↔ coordenadas |

## Perfil y cuaderno (cabecera `Authorization: Bearer <clave>`)
| Método y ruta | Descripción |
|---|---|
| `POST /api/profile` | Crea un perfil anónimo y devuelve la clave (solo se muestra una vez) |
| `GET/PUT/DELETE /api/profile` | Ajustes (QTH, favoritos) / borrar todo |
| `GET /api/qso` · `POST /api/qso` | Listar (filtros `q`, `band`, `mode`, `status`) / añadir contacto |
| `PATCH/DELETE /api/qso/{id}` | Marcar QSL enviada/recibida, editar, borrar |
| `GET /api/qso/stats` · `GET /api/qso/adif` · `POST /api/qso/import` | Estadísticas, exportar y importar ADIF |

## Administración (`X-Admin-Token`)
`POST /api/admin/repeaters`, `POST /api/admin/repeaters/sync`, `POST /api/admin/repeaters/{id}/coverage`.

## Cuenta, IA y asistente
| Método y ruta | Descripción |
|---|---|
| `GET /api/me/settings` · `PUT /api/me/settings` | IA sí/no, proveedor, modelo, claves guardadas (solo «••••1234») y **Mi ubicación** (`home` {lat, lon, name}; `null` la borra) |
| `PUT/DELETE /api/me/key` · `GET /api/me/models?provider=` · `POST /api/me/test` | Clave propia (cifrada), catálogo de modelos y prueba de conexión |
| `POST /api/assistant` | Chat: `{messages, lat?, lon?, name?, section?, home?}`; usa tu proveedor y modelo, el lugar abierto y tu ubicación |
| `DELETE /api/me` | Borra tus ajustes y claves |

Desde el servicio MCP (red interna) las rutas `/api/me/*`, `/api/assistant` y `/api/forecast/ai` aceptan `X-Admin-Token` + `X-Act-As: <sub>` para actuar en nombre de la persona
autenticada con OAuth; desde fuera, solo con la sesión.
