# API de Meteo Guilda · Radio Guilda

Esquema OpenAPI completo: `/api/openapi.json`. Respuestas en JSON. Los límites por IP devuelven `429`.
Las mismas funciones están disponibles como herramientas del [servidor MCP](../backend/meteoguilda/mcp_server.py).

## Meteo
| Método y ruta | Descripción |
|---|---|
| `GET /api/places` | Los 6 lugares principales con instantánea |
| `GET /api/search?q=` | Buscar cualquier lugar (coordenadas y altitud) |
| `GET /api/forecast?place=slug` o `?lat=&lon=&name=` | Pronóstico con lectura de IA o reglas, y datos de origen |
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
