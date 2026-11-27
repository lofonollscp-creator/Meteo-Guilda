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
| `GET /api/cuota` | Uso de Open-Meteo (peticiones por minuto, hora y día frente al límite gratuito) y si hay freno por 429 |
| `GET /api/compare?pts=lat,lon,nombre;...` | Compara 2-4 lugares: cifras de 48 h, series y veredicto |
| `GET /api/me/ai-status` | Estado de los modelos gratuitos de la IA (disponible / en pausa y minutos que faltan); requiere sesión |
| `GET /api/mar?lat=&lon=` | Estado del mar: oleaje, mar de fondo, periodo y agua, ahora y 48 h (`hay_mar=false` tierra adentro) |
| `GET /api/air?lat=&lon=` | Calidad del aire (EAQI), UV y polen: ahora, máximo de hoy y 24 h |
| `GET /api/reverse?lat=&lon=` | Nombre del sitio (municipio, comarca, región) y locator |
| `GET /api/raw?lat=&lon=` | Datos crudos de modelos y balizas, sin texto de IA |
| `GET /api/snapshot?pts=lat,lon;lat,lon` | Instantánea de hasta 15 puntos |
| `GET /api/stations?south=&west=&north=&east=` | Estaciones APRS con calidad |
| `GET /api/observations?lat=&lon=&km=` | Estaciones cercanas a un punto |
| `POST /api/route` | Ruta (a pie/coche/GPX) con meteo a la hora de paso, riesgo y ventanas; `salida_por_tramos` trae el semáforo por tramos (perfil monte o carretera) y el equipo sugerido |
| `POST /api/salidas` | Planificador de salidas: `{perfil: monte\|carretera\|mar, ruta_id? \| waypoints, depart?, nudos?}` → semáforo por tramo con motivos, tramos críticos, mejor ventana (36 h) y equipo sugerido. Reglas, no IA |
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
| `GET /api/aprs?bbox=sur,oeste,norte,este` o `?lat=&lon=&km=` (`&max_age_min=60`) | Estaciones APRS recientes (última posición: tipo, velocidad, rumbo, altitud, comentario). Zona: 60 km alrededor de los lugares principales; se guarda 24 h |
| `GET /api/dxcluster?banda=&modo=&continente=&buscar=&limite=` | Spots del clúster DX (HamQTH; reserva DX Summit). Una descarga compartida cada 2 min como mucho |
| `GET /api/avisos?lat=&lon=` | Avisos oficiales vigentes (AEMET) del punto + enlaces; requiere `AEMET_API_KEY` para los avisos |
| `GET /api/precision?lat=&lon=&dias=` | Qué modelo acierta más la temperatura a 24 h en un lugar extra verificado a ≤ 5 km o, si no, en el lugar principal más cercano (≤ 60 km), frente a estaciones XEMA/METAR; vacío hasta reunir ≥ 8 comparaciones por modelo |
| `GET /api/verificacion/informe?mes=AAAA-MM` | Informe mensual por lugar (principales y los tuyos): ranking a 24 y 12 h, sesgo, % a ±2 °C, ganador y mes anterior; solo lee la base de datos |
| `GET\|PUT /api/verificacion/mis-lugares` | Tus lugares extra a verificar (0-5; tope global de 40). Se consultan una vez cada 6 h y solo si la cuota diaria de Open-Meteo va por debajo del 60 % |
| `GET /api/push/config` · `PUT\|DELETE /api/push/suscripcion` · `POST /api/push/estado\|paso\|prueba` | Notificaciones push (requieren sesión y `MG_VAPID_*`): suscribir el navegador con sus preferencias, recordar un paso de satélite 5 min antes y enviar una de prueba |
| `GET /api/alertas` · `POST /api/alertas` · `PATCH\|DELETE /api/alertas/{id}` · `GET /api/alertas/{id}/comprobar` · `PUT /api/alertas/prefs` | Alertas de radio y salidas de la persona (tropo, HF, satélites, tiempo en el QTH o en una ruta guardada), con tope diario y horario de silencio; ver `alertas.py` |
| `GET\|POST /api/rutas` · `GET\|DELETE /api/rutas/{id}` | Rutas guardadas de la persona (2-10 puntos, modo `foot`, `car` o `sea`; máximo 20) |
| `GET\|PUT /api/me/memoria` · `POST /api/me/memoria/notas` · `DELETE /api/me/memoria/notas[/{id}]` | Memoria de preferencias del asistente (opt-in, 20 notas × 140 car., caduca a los 12 meses; rechaza datos sensibles o identificadores) |
| `GET /api/me/datos` | Acceso y portabilidad (RGPD): todo lo que se guarda de la persona en JSON descargable, sin claves de API |
| `GET /api/oficial?lat=&lon=` | Predicción oficial a 8 días del Meteocat (solo Cataluña; requiere `METEOCAT_API_KEY`; plan de 100 consultas/mes: caché 18 h y topes propios de 90/mes y 5/día) |
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
| `POST /api/qsl/{lotw\|eqsl\|qrz}/subir` · `POST /api/qsl/{servicio}/confirmaciones` | Subir los contactos pendientes e importar confirmaciones (además, sesión o identidad de la cuenta con el servicio conectado; ver [QSL-SERVICIOS.md](QSL-SERVICIOS.md)) |

`GET /api/qsl/servicios` (estado) y `PUT|DELETE /api/qsl/servicios/{servicio}` (credenciales cifradas, **solo con la sesión de la web**) van con la cuenta.

## Descargas por enlace
`GET /api/descargar/{token}`: fichero que preparó el MCP (ADIF, respuestas grandes). **Público** (sin sesión), pero el token va firmado y caduca en una hora; 404 si es falso o ha caducado.
`POST /api/descargas` (solo `X-Admin-Token`, uso interno del MCP) guarda el contenido y devuelve `{enlace, nombre_archivo, tipo_mime, tamano, caduca_en_minutos}`.

## Administración (`X-Admin-Token`)
`POST /api/admin/repeaters`, `POST /api/admin/repeaters/sync`, `POST /api/admin/repeaters/{id}/coverage`.

## Cuenta, IA y asistente
| Método y ruta | Descripción |
|---|---|
| `GET /api/me/settings` · `PUT /api/me/settings` | IA sí/no, proveedor, modelo, claves guardadas (solo «••••1234») y **Mi ubicación** (`home` {lat, lon, name}; `null` la borra) |
| `PUT/DELETE /api/me/key` · `GET /api/me/models?provider=` · `POST /api/me/test` | Clave propia (cifrada), catálogo de modelos y prueba de conexión |
| `POST /api/assistant` | Chat: `{messages, lat?, lon?, name?, section?, home?}`; usa tu proveedor y modelo, el lugar abierto y tu ubicación. **Con herramientas**: el modelo puede buscar pueblos por nombre y llamar a previsión, avisos, aire, mar, comparar, actividad, ruta, observaciones, propagación, HF, repetidores, satélites, astro, portátil, ventanas y locator (hasta 4 vueltas). Devuelve `{answer, model, herramientas[], lugares[]}` |
| `POST /api/assistant/stream` | Lo mismo que `/api/assistant` pero en flujo (SSE, `text/event-stream`): eventos `{t:"herr",items}` con las herramientas que va consultando y `{t:"fin",answer,model,herramientas,lugares,tarjetas}` al terminar (o `{t:"error",status,detail}`). Las acciones que cambian datos (`guardar_lugar`, `usar_como_mi_ubicacion`, `anotar_qso`, `avisar_paso`) solo devuelven una tarjeta con botón: no se ejecutan hasta que la persona lo confirma. |
| `DELETE /api/me` | Borra tus ajustes y claves |

Desde el servicio MCP (red interna) las rutas `/api/me/*`, `/api/assistant` y `/api/forecast/ai` aceptan `X-Admin-Token` + `X-Act-As: <sub>` para actuar en nombre de la persona
autenticada con OAuth; desde fuera, solo con la sesión.
