# Mapa de funcionalidades: web ↔ API ↔ MCP

Regla de la casa: **todo lo que se puede hacer en la web se puede hacer también por la API y por el MCP**, salvo lo que se deja solo en la web a propósito
(secretos y acciones destructivas de la cuenta; ver «Excepciones»). Al añadir una función, hay que añadir las tres columnas y este documento.

- **API**: `https://radio.guildawork.com/api/…` (con la sesión de Guilda Work; esquema en `/api/openapi.json`; ver [API.md](API.md)).
- **MCP**: `https://radio.guildawork.com/mcp` (OAuth con la cuenta de Guilda Work). Las herramientas «de cuenta» actúan **en nombre de la persona** que se
  autenticó (su proveedor de IA, sus ajustes, su ubicación); con la clave fija `MG_MCP_TOKEN` solo funcionan las públicas.

## Meteo
| En la web | API | MCP |
|---|---|---|
| Lugares principales y su resumen | `GET /api/places` | `list_places` |
| Buscar un lugar | `GET /api/search?q=` | `search_place` |
| Previsión de un lugar (datos de 8 modelos, resumen por reglas, observaciones) y botón ↻ recargar | `GET /api/forecast?place=` / `?lat=&lon=` (`&refresh=1`) | `get_ai_forecast` (`refresh`) |
| Datos crudos con el valor de cada modelo (`por_modelo`) | `GET /api/raw?lat=&lon=` | `get_forecast_data` |
| Gráfico «Contraste de modelos» | `por_modelo` en `/api/forecast` y `/api/raw` | `get_forecast_data` |
| «Quién acierta más» (en Contraste de modelos: error de cada modelo a 24 h en la zona) | `GET /api/precision?lat=&lon=&dias=` | `model_accuracy` |
| Botón «Quién acierta más: informe del mes» (por lugar y general, a 24 y 12 h, con el mes anterior) | `GET /api/verificacion/informe?mes=AAAA-MM` | `model_verification_report` (OAuth) |
| Mis lugares verificados (hasta 5: este lugar, Mi ubicación o favoritos) | `GET/PUT /api/verificacion/mis-lugares` | `get_my_verified_places`, `set_my_verified_places` (OAuth) |
| Lectura redactada por IA (botón «Generar lectura con IA», **a petición**) | `GET /api/forecast/ai?…` (`&solo_cache=1` no gasta nada) | `generate_ai_forecast` (OAuth) |
| Mis lugares / tarjetas con instantánea | `GET /api/snapshot?pts=` | `get_snapshot` |
| Observaciones reales (APRS, METAR, XEMA) | `GET /api/observations`, `GET /api/stations` | `get_nearby_observations`, `get_stations` |
| Mejor momento para… (senderismo, ciclismo, dron, colada, estrellas, playa) | `GET /api/activities`, `GET /api/activity` | `list_activities`, `plan_activity` |
| Aire, UV y polen | `GET /api/air` | `get_air_quality` |
| Comparar lugares | `GET /api/compare` | `compare_places` |
| Estado del mar | `GET /api/mar` | `get_sea_state` |
| Sol y Luna | `GET /api/astro` | `sun_moon` |
| Rutas con el tiempo a la hora de paso | `POST /api/route` | `plan_route` |
| **Mi ubicación** (botón «Usar mi ubicación», nombre del sitio) | `GET/PUT /api/me/settings` (campo `home`), `GET /api/reverse` | `get_my_location`, `set_my_location`, `reverse_geocode` |
| Previsión (y aire) de mi ubicación | `/api/raw` + `/api/air` con `home` | `get_forecast_here` (OAuth) |

## Radio
| En la web | API | MCP |
|---|---|---|
| Repetidores en el mapa / búsqueda / ficha | `GET /api/repeaters`, `/api/repeaters/near`, `/api/repeaters/{id}` | `search_repeaters`, `find_repeaters`, `get_repeater` |
| Cobertura estimada de un repetidor | `POST /api/repeaters/{id}/coverage` | `get_repeater(compute_coverage=true)` |
| Resumen del catálogo | `GET /api/repeaters/stats` | `repeater_stats` |
| Propagación tropo (VHF/UHF) | `GET /api/propagation` | `get_vhf_propagation` |
| Propagación HF (Kp, flujo solar) | `GET /api/solar` | `get_hf_conditions` |
| Satélites: lista y pasos | `GET /api/satellites`, `/api/satellites/passes` | `list_satellites`, `satellite_passes` |
| Operación en portátil | `GET /api/portable` | `portable_operation_window` |
| Avisos oficiales de un punto (AEMET) | `GET /api/avisos` | `get_official_warnings` |
| Ventanas de operación (HF, tropo, satélites, campo) | `GET /api/radio/ventanas` | `radio_operating_windows` |
| Locator Maidenhead | `GET /api/locator` | `maidenhead` |
| Mi QTH (el mismo que «Mi ubicación») | `PUT /api/me/settings` (`home`) y perfil `PUT /api/profile` | `set_my_location`, `profile_set` |

## Alertas de radio y salidas (Ajustes → Avisos; de la cuenta, llegan a todos tus dispositivos con notificaciones)
| En la web | API | MCP |
|---|---|---|
| Lista de alertas, límites, últimas enviadas y catálogo de tipos | `GET /api/alertas` | `list_alerts` (OAuth) |
| Nueva alerta: tropo VHF/UHF, HF (mejora o tormenta), pasos de satélite, tiempo u aviso oficial en el QTH o en una ruta guardada | `POST /api/alertas` | `create_alert` (OAuth) |
| Pausar / reanudar / renombrar | `PATCH /api/alertas/{id}` | `update_alert` (OAuth) |
| Borrar | `DELETE /api/alertas/{id}` | `delete_alert` (OAuth) |
| «Comprobar» (¿saltaría ahora? sin mandar nada) | `GET /api/alertas/{id}/comprobar` | `check_alert` (OAuth) |
| Máximo de avisos al día y horario de silencio | `PUT /api/alertas/prefs` | `set_alert_limits` (OAuth) |

## Rutas guardadas (Rutas → «Guardar ruta» y «Mis rutas guardadas»)
| En la web | API | MCP |
|---|---|---|
| Lista de rutas guardadas | `GET /api/rutas`, `GET /api/rutas/{id}` | `list_saved_routes` (OAuth) |
| Guardar la ruta calculada (con el mismo nombre, se sustituye) | `POST /api/rutas` | `save_route` (OAuth) |
| Borrar una ruta guardada (y sus alertas) | `DELETE /api/rutas/{id}` | `delete_saved_route` (OAuth) |

## Cuaderno QSL y sincronización (clave del cuaderno)
| En la web | API | MCP |
|---|---|---|
| Activar sincronización (clave secreta) | `POST /api/profile` | `logbook_create_profile` |
| Favoritos y QTH sincronizados | `GET/PUT /api/profile` | `profile_get`, `profile_set` |
| Listar / filtrar contactos | `GET /api/qso` | `logbook_list` |
| Añadir un contacto (avisa de duplicados) | `POST /api/qso` | `logbook_add` |
| ¿Ya lo he trabajado? | `GET /api/qso/worked` | `logbook_worked` |
| Editar / marcar QSL | `PATCH /api/qso/{id}` | `logbook_update`, `logbook_mark_qsl` |
| Borrar un contacto (la web pregunta; el MCP pide confirmación a la persona por elicitación) | `DELETE /api/qso/{id}` | `logbook_delete` |
| Estadísticas, ADIF (importar/exportar) | `GET /api/qso/stats`, `/api/qso/adif`, `POST /api/qso/import` | `logbook_stats`, `logbook_export_adif` (enlace de descarga), `logbook_import_adif` |
| Servicios: estado de LoTW, eQSL y QRZ | `GET /api/qsl/servicios` | `qsl_services_status` (OAuth) |
| Servicios: «Subir pendientes» | `POST /api/qsl/{servicio}/subir` | `qsl_upload` (OAuth + clave del cuaderno) |
| Servicios: «Bajar confirmaciones» | `POST /api/qsl/{servicio}/confirmaciones` | `qsl_import_confirmations` (OAuth + clave del cuaderno) |

## Cuenta, IA y asistente
| En la web | API | MCP |
|---|---|---|
| Ajustes: IA interna sí/no, proveedor, modelo | `GET/PUT /api/me/settings` | `get_account_settings`, `set_ai_settings` (OAuth) |
| Ver modelos de un proveedor | `GET /api/me/models?provider=` | `list_ai_models` (OAuth) |
| Probar la conexión de IA | `POST /api/me/test` | `test_ai_connection` (OAuth) |
| Asistente de chat (con el lugar abierto y Mi ubicación) | `POST /api/assistant` (y `/api/assistant/stream` en flujo) | `ask_assistant` (OAuth) |
| Estado del servicio | `GET /api/health` | `server_status` |

## Cómo responde el MCP a lo grande y a lo que borra
- **Descargas por enlace**: `logbook_export_adif` no devuelve el ADIF, sino un enlace `https://radio.guildawork.com/api/descargar/<token>` que **caduca en una hora**
  (se puede abrir varias veces; no pide sesión, pero solo sirve para ese fichero). Las respuestas de más de ~60 KB (`logbook_list`, `get_stations`,
  `search_repeaters`, `get_repeater` con cobertura, `satellite_passes`, `list_satellites`, `get_forecast_data`, `plan_route`…) llegan igual: enlace a un JSON y un resumen.
  La API guarda el fichero con `POST /api/descargas` (solo el servicio MCP, con `MG_ADMIN_TOKEN`) y lo borra a las 2 horas. Ver `descargas.py`.
- **Confirmación antes de borrar**: `logbook_delete` pregunta a la persona con elicitación (SDK 2: `Resolve`/`Elicit`; con clientes del protocolo 2026-07-28 la pregunta
  viaja como `input_required` y con los anteriores, en mitad de la llamada). Si dice que no, no se borra nada. Si el cliente no admite elicitación, se fía de su
  propia aprobación (la herramienta va marcada `destructiveHint`).

## Excepciones (solo web o solo administración)
- **Guardar o borrar claves de API** (`PUT/DELETE /api/me/key`): no pasan por el MCP para que una clave nunca viaje por un chat de IA.
- **Guardar o quitar las credenciales de LoTW, eQSL y QRZ** (`PUT/DELETE /api/qsl/servicios/{servicio}`): solo con la sesión de la web; al MCP le responde 403. Ver [QSL-SERVICIOS.md](QSL-SERVICIOS.md).
- **Borrar mis datos de la cuenta** (`DELETE /api/me`) y **cerrar sesión**: acciones de la persona en la web.
- **Administración** (`/api/admin/*`: sincronizar repetidores, calcular cobertura, alta manual) y **OAuth/login** (`/api/oauth/*`, `/api/auth/*`): con `MG_ADMIN_TOKEN` o de uso interno.
- **Solo en el navegador, sin datos del servidor**: sección «Aprende», herramientas de radio (Morse con audio, código Q, fonético, CTCSS, dBm, longitud de antena), generar la
  tarjeta QSL en imagen y exportar repetidores a CHIRP (se construyen con los datos de `/api/repeaters`).
