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

## Cuaderno QSL y sincronización (clave del cuaderno)
| En la web | API | MCP |
|---|---|---|
| Activar sincronización (clave secreta) | `POST /api/profile` | `logbook_create_profile` |
| Favoritos y QTH sincronizados | `GET/PUT /api/profile` | `profile_get`, `profile_set` |
| Listar / filtrar contactos | `GET /api/qso` | `logbook_list` |
| Añadir un contacto (avisa de duplicados) | `POST /api/qso` | `logbook_add` |
| ¿Ya lo he trabajado? | `GET /api/qso/worked` | `logbook_worked` |
| Editar / marcar QSL | `PATCH /api/qso/{id}` | `logbook_update`, `logbook_mark_qsl` |
| Borrar un contacto | `DELETE /api/qso/{id}` | `logbook_delete` |
| Estadísticas, ADIF (importar/exportar) | `GET /api/qso/stats`, `/api/qso/adif`, `POST /api/qso/import` | `logbook_stats`, `logbook_export_adif`, `logbook_import_adif` |

## Cuenta, IA y asistente
| En la web | API | MCP |
|---|---|---|
| Ajustes: IA interna sí/no, proveedor, modelo | `GET/PUT /api/me/settings` | `get_account_settings`, `set_ai_settings` (OAuth) |
| Ver modelos de un proveedor | `GET /api/me/models?provider=` | `list_ai_models` (OAuth) |
| Probar la conexión de IA | `POST /api/me/test` | `test_ai_connection` (OAuth) |
| Asistente de chat (con el lugar abierto y Mi ubicación) | `POST /api/assistant` | `ask_assistant` (OAuth) |
| Estado del servicio | `GET /api/health` | `server_status` |

## Excepciones (solo web o solo administración)
- **Guardar o borrar claves de API** (`PUT/DELETE /api/me/key`): no pasan por el MCP para que una clave nunca viaje por un chat de IA.
- **Borrar mis datos de la cuenta** (`DELETE /api/me`) y **cerrar sesión**: acciones de la persona en la web.
- **Administración** (`/api/admin/*`: sincronizar repetidores, calcular cobertura, alta manual) y **OAuth/login** (`/api/oauth/*`, `/api/auth/*`): con `MG_ADMIN_TOKEN` o de uso interno.
- **Solo en el navegador, sin datos del servidor**: sección «Aprende», herramientas de radio (Morse con audio, código Q, fonético, CTCSS, dBm, longitud de antena), generar la
  tarjeta QSL en imagen y exportar repetidores a CHIRP (se construyen con los datos de `/api/repeaters`).
