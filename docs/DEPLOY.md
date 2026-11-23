# Despliegue en el VPS de Hetzner (compartido) · radio.guildawork.com

> **Servidor actual:** el de PRE (`pre.guildawork.com`, 62.238.102.126), en `/home/guilda/meteo-guilda`, publicado por el Caddy del host (`/etc/caddy/sitios.d/radio.caddy`). Para actualizar usa [`desplegar_produccion.sh`](#9-desplegar_produccionsh) y para conectar el MCP, [§11](#11-conectar-el-mcp-con-oauth).

Pensado para convivir con otros trabajos en el mismo servidor: **no abre puertos públicos**, cada servicio tiene su
límite de memoria (≈ 1,3 GB en total en el peor caso; en reposo ronda los 600–800 MB) y el TLS lo hace el proxy que ya tengas.

## 1. Requisitos
- Docker y Docker Compose v2 (`docker compose version`). Si no los tienes: `curl -fsSL https://get.docker.com | sudo sh`.
- Un registro DNS `A` (y `AAAA` si usas IPv6) de `radio.guildawork.com` hacia la IP del VPS.
- Un proxy inverso en el host (nginx o Caddy). Ya tienes uno si el VPS sirve otras webs.

## 2. Instalar
```bash
git clone <repo> /opt/guilda && cd /opt/guilda
cp .env.example .env && nano .env        # POSTGRES_PASSWORD, MG_MCP_TOKEN, MG_ADMIN_TOKEN, (OPENROUTER_API_KEY)
docker compose up -d --build
docker compose ps && curl -s http://127.0.0.1:8088/api/health        # {"ok":true,"db":true}
```
El primer arranque descarga el catálogo de repetidores (BrandMeister + HearHam, ~1 min) y empieza a recoger balizas APRS.
Si `8088` está ocupado, cambia `MG_HTTP_PORT` en `.env` y en el fichero del proxy.

## 3. Publicar con TLS (elige una)
**Caddy del host** (el caso de este servidor): `deploy/Caddyfile.host-snippet` → `/etc/caddy/sitios.d/radio.caddy` y `systemctl reload caddy`. **nginx**: `deploy/nginx-radio.guildawork.com.conf` (incluye certbot).
Comprueba que la cabecera `X-Forwarded-For` llega a la API (la usa para limitar peticiones por IP).

## 4. Qué se está ejecutando
| Servicio | Función | Memoria máx. |
|---|---|---|
| `db` | PostgreSQL 16 + PostGIS | 512 MB |
| `web` | Caddy interno: web estática, `/api`, `/mcp` | 128 MB |
| `api` | FastAPI; regenera pronósticos cada 3 h y sincroniza repetidores cada 24 h | 512 MB |
| `aprs` | Ingestor APRS-IS (solo lectura) | 192 MB |
| `mcp` | Servidor MCP por HTTP con token | 192 MB |

## 5. Operación
```bash
git pull && docker compose up -d --build                     # actualizar
docker compose logs -f api aprs                              # registros
docker compose exec db pg_dump -U mg meteoguilda | gzip > /root/backups/guilda-$(date +%F).sql.gz   # copia
curl -X POST -H "X-Admin-Token: $MG_ADMIN_TOKEN" http://127.0.0.1:8088/api/admin/repeaters/sync     # forzar sincronización de repetidores
```
Las observaciones APRS se purgan solas a los 7 días. El cuaderno QSL sincronizado vive en la tabla `qsos`: incluye esa base en tus copias.

## 6. Instalar como app y MCP
- **App (PWA)**: se instala desde el navegador (icono de instalar en la barra lateral; en iPhone, Compartir → Añadir a pantalla de inicio).
- **MCP**: la URL es `https://radio.guildawork.com/mcp` y se conecta con el inicio de sesión de Guilda Work (OAuth, ver [§11](#11-conectar-el-mcp-con-oauth)). También vale la clave fija: `claude mcp add --transport http guilda https://radio.guildawork.com/mcp --header "Authorization: Bearer $MG_MCP_TOKEN"`.
  Para el cuaderno, pasa la clave del cuaderno en cada herramienta (`key`) o define `MG_LOGBOOK_KEY` en el servicio `mcp`.
- **API**: `https://radio.guildawork.com/api/openapi.json` (ver también `docs/API.md`).

## 7. Servicios externos y uso razonable
Con poco tráfico basta con los servicios gratuitos, pero conviene saber de qué depende cada cosa:
| Dependencia | Uso | Si crece el tráfico |
|---|---|---|
| Open-Meteo (modelos, elevación, geocodificación) | Gratis para uso **no comercial** con límite diario por IP | Instancia propia o plan de pago |
| OpenStreetMap (tiles) | Mapa base | Tiles propios (Protomaps) o proveedor con clave |
| Valhalla público (FOSSGIS) | Rutas | Valhalla propio con el extracto de OSM |
| CelesTrak | TLE de satélites (caché 12 h + copia en BD) | — |
| NOAA SWPC | Kp y flujo solar | — |
| BrandMeister y HearHam | Repetidores (1 descarga/día) | — |
| OpenRouter | Texto de IA (opcional) | Cuota gratuita limitada |

## 8. Problemas frecuentes
- *El mapa sale en blanco*: el proxy del host no debe añadir una CSP propia; la CSP ya la envía el servicio `web`.
- *`429` en rutas o cobertura*: límite por IP o cuota diaria de Open-Meteo; se recupera solo.
- *Sin repetidores*: mira `docker compose logs api | grep repetidores`; fuerza la sincronización con el comando de arriba.
- *Satélites sin datos*: CelesTrak puede rechazar temporalmente la conexión; se usa la última copia guardada.

## 9. `desplegar_produccion.sh`
Un solo comando para actualizar el servidor (lo lanza `guilda`; si lo lanzas como root se reejecuta como `guilda`):
```bash
cd /home/guilda/meteo-guilda && ./desplegar_produccion.sh
```
| Opción | Qué hace |
|---|---|
| *(ninguna)* | `git pull --ff-only`, crea el cliente de Hydra si falta, `docker compose up -d --build`, verifica |
| `--probar` | antes de desplegar pasa los tests en una copia aislada con una PostGIS desechable; si fallan, no despliega |
| `--sin-pull` | despliega lo que haya en el directorio (cambios locales o commits todavía sin subir a GitHub) |
| `--solo-verificar` | no cambia nada: comprueba salud, contenedores, web, OAuth, MCP, repetidores y HTTPS público |

Se detiene (sin tocar nada) si falta `.env`, si hay cambios sin commitear, si el repositorio local y GitHub han divergido o si los tests fallan.
Al final imprime el commit desplegado y el anterior, con la orden para volver atrás. Reinicia el Caddy interno solo cuando cambia `deploy/Caddyfile`
(tiene el admin desactivado, así que no admite `reload`). **No toca el Caddy del host**: si su bloque difiere de `deploy/Caddyfile.host-snippet`, avisa.

## 10. Acceso: login obligatorio (web, API y MCP)
**Toda** `radio.guildawork.com` exige iniciar sesión con la cuenta de Guilda Work (Hydra del servidor de identidad), igual que La cesta:
- **Web y API (`/api/*`)**: el Caddy interno consulta `/api/auth/check` (`forward_auth`) antes de servirlas. Sin sesión, el navegador va a `/api/auth/login` → Hydra → `/api/auth/callback` y vuelve a la página que pedía;
  las peticiones de la app reciben `401` (cabecera `x-auth: login`) y la app redirige al login. La sesión es una cookie `mg_session` (HttpOnly, Secure, 30 días; en BD solo su hash, tabla `web_sessions`).
- **Públicas**: `/api/auth/*`, `/api/oauth/*`, `/.well-known/oauth-*`, `/api/health` y `/mcp` (que valida su propio token).
- **MCP y API interna** (red de Docker, `http://api:8000`) no pasan por la puerta: el MCP se protege con su token OAuth.
- **Cerrar sesión**: botón «Cerrar sesión de Guilda» en el diálogo de la llave (arriba), o `https://radio.guildawork.com/api/auth/logout`.
- **Revocar a alguien**: `docker compose exec db psql -U mg meteoguilda -c "DELETE FROM web_sessions WHERE email='a@x.com'"` (≤ 30 s, caché de sesión).
- **Restringir**: `MG_OAUTH_ALLOWED_EMAILS=a@x.com,b@y.com` en `.env` (vale para web y MCP) y `./desplegar_produccion.sh`. Vacío = cualquier cuenta de Guilda Work.
- **Hydra**: el cliente `radio-guilda-web` necesita dos direcciones de retorno, `…/api/auth/callback` (web) y `…/api/oauth/callback` (MCP); `desplegar_produccion.sh` las crea o corrige.
- **App instalada (PWA)**: el service worker guarda la carcasa para uso sin conexión, así que una sesión caducada se nota al primer dato que pide la app (redirige al login).

## 11. Conectar el MCP con OAuth
El servicio hace de servidor de autorización OAuth 2.1 (registro dinámico de clientes, código + PKCE S256, tokens de 1 h con renovación de 90 días
que rota) y la persona se identifica con su cuenta de Guilda Work a través del Hydra del servidor de identidad (`MG_OIDC_ISSUER`), igual que La cesta.
- **Enlace para el cliente MCP:** `https://radio.guildawork.com/mcp`.
  - **Claude (web/escritorio):** Ajustes → Conectores → *Añadir conector personalizado* → pega el enlace → Conectar. Se abre el login de Guilda Work y una pantalla de consentimiento.
  - **Claude Code:** `claude mcp add --transport http radio-guilda https://radio.guildawork.com/mcp` y después `/mcp` → *Authenticate*.
- **Flujo:** el cliente descubre el OAuth en `/.well-known/oauth-protected-resource/mcp` y `/.well-known/oauth-authorization-server`, se registra en `/api/oauth/register`,
  autoriza en `/api/oauth/authorize` (→ login en Hydra → `/api/oauth/callback` → consentimiento), canjea el código en `/api/oauth/token` y usa el token en `/mcp`.
  El servicio `mcp` valida cada token contra `/api/oauth/introspect` (solo con `MG_ADMIN_TOKEN`; caché de 60 s).
- **Quién puede entrar:** el mismo control que la web (§10): cualquier cuenta de Guilda Work del servidor de identidad. Si ya tienes sesión en la web, la autorización del MCP salta el login y va directa al consentimiento. Para restringirlo, `MG_OAUTH_ALLOWED_EMAILS=a@x.com,b@y.com` en `.env` (se aplica al autorizar) y `docker compose up -d`.
- **Revocar:** `docker compose exec db psql -U mg meteoguilda -c "UPDATE oauth_tokens SET revoked_at=now() WHERE email='a@x.com'"` (deja de valer en ≤ 60 s). `DELETE FROM oauth_clients` retira clientes registrados.
- **Hydra:** el cliente público `radio-guilda-web` (PKCE, `skip_consent`, retorno `https://radio.guildawork.com/api/oauth/callback` y `/api/auth/callback`) lo crea `desplegar_produccion.sh` por la API de administración de Hydra
  (`MG_HYDRA_ADMIN_URL`, por defecto `http://127.0.0.1:4445`). Si Hydra está en otra máquina, créalo a mano con esos mismos datos.
- **Cuaderno QSL:** el login identifica a la persona, pero el cuaderno sigue usando su clave secreta (`key` en las herramientas o `MG_LOGBOOK_KEY`).
- **Mover a PRO:** cambia `MG_OIDC_ISSUER` al Hydra de PRO, vuelve a lanzar `desplegar_produccion.sh` y listo; los tokens emitidos con el otro servidor no valen.
- **Clave de IA:** `OPENROUTER_API_KEY` reutiliza la de La cesta (copiada a `.env`); si la rotas allí, cámbiala también aquí y `docker compose up -d api`.
- **Mistral (desde el 23-nov-2026):** con `MISTRAL_API_KEY` en `.env` (la misma que La cesta y Guilda Work) la IA del servidor es Mistral (`MG_MISTRAL_MODEL`, por defecto `mistral-small-latest`) y los modelos gratuitos de OpenRouter dejan de usarse. Cada persona puede además poner su propia clave de Mistral en Ajustes.

## 12. Ajustes de la cuenta, IA y asistente (v0.4)
- **Ajustes** (icono del engranaje, arriba): pestañas *IA*, *Cuenta* y *Sincronización y API*.
  - **IA interna sí/no**: desactivada, la lectura del pronóstico muestra solo el resumen automático por reglas y el botón del asistente se oculta (es por persona; se guarda en el servidor).
  - **Proveedor y modelo**: *Servidor* (modelos gratuitos de OpenRouter con la clave del servidor), o la clave propia de **OpenRouter**, **OpenAI** o **Anthropic**. «Ver modelos» lista el catálogo del proveedor y «Probar conexión» hace una llamada mínima.
  - **Claves propias**: se guardan cifradas con Fernet (`MG_SECRET_KEY`) en `user_settings.keys` y nunca se devuelven (solo «••••1234»). Sin clave utilizable se vuelve al proveedor del servidor, nunca a la clave de otra persona. `desplegar_produccion.sh` genera `MG_SECRET_KEY` si falta; **no la cambies ni la pierdas** (invalidaría las claves guardadas).
  - **Cuenta**: borrar el historial de búsquedas del dispositivo, borrar ajustes y claves del servidor (`DELETE /api/me`), cerrar sesión.
- **Asistente** (icono de chat): panel lateral (pantalla completa en móvil) que recibe el lugar que estás viendo: datos de los modelos (sin el detalle por modelo) y, en Radio, los repetidores a menos de 50 km. Usa el proveedor y el modelo de tus ajustes. La conversación se guarda solo en el dispositivo.
- **Lectura de IA aparte**: `GET /api/forecast` devuelve al instante los datos y el resumen por reglas; la lectura redactada llega después por `GET /api/forecast/ai` (caché de 3 h por lugar y configuración; si falla se recuerda 5 min). Antes la página esperaba a la IA, que podía tardar minutos.
- API: `GET/PUT /api/me/settings`, `PUT/DELETE /api/me/key`, `GET /api/me/models?provider=`, `POST /api/me/test`, `DELETE /api/me`, `POST /api/assistant`. Todas exigen sesión (como el resto de `/api/*`).

## 13. Modelos y fuentes de datos
- **8 modelos** en la previsión puntual: ECMWF IFS y AIFS (IA), DWD ICON, NOAA GFS, Météo-France (AROME/ARPEGE), ECCC GEM, UK Met Office y KNMI HARMONIE. Rutas y portátil usan los 4 primeros (decenas de puntos por petición). La web los contrasta en «Contraste de modelos» (una línea por modelo, discrepancia media) y el MCP los devuelve en `por_modelo`.
- **Observaciones reales** (campo `fuente`): balizas **APRS** (control de calidad propio), **METAR** de aeropuertos (aviationweather.gov, todo el mundo) y estaciones **XEMA** del Meteocat (datos abiertos de Transparència Catalunya; solo se consulta en Cataluña). Sin claves; caché de 10 min; si una fuente falla se ignora.
- **Cuota de Open-Meteo**: cobra por modelo (8 modelos ≈ coste 8,8 por consulta; límite gratuito 600/min y 10 000/día por IP). Por eso: caché de 2 h, recarga manual (botón ↻) con enfriamiento de 5 min, datos antiguos si la fuente devuelve 429 y refresco horario solo de los 6 lugares principales. Si el tráfico crece: instancia propia o clave comercial de Open-Meteo.
- **Pendiente de claves** (gratuitas pero con registro por correo): **AEMET OpenData** y **API del Meteocat** (predicción oficial) y **Météo-France** (portal API). Se pueden añadir como una fuente más cuando haya clave.

## 14. Rendimiento y pila de pruebas
- La web ya no descarga MapLibre (800 KB) antes de pintar: se carga en reposo (escritorio) o al abrir la pestaña Mapa (móvil); las secciones Rutas, Radio y Aprende se cargan al entrar. En móvil con red lenta simulada: datos en pantalla de 3,7 s a 1,7 s y de 414 a 194 KB.
- **Pila de pruebas** (no toca producción): `docker compose -f docker-compose.qa.yml -p mgqa up -d --build` → `http://127.0.0.1:8099` (sin login, BD desechable, `MG_QA=1`); `docker compose -f docker-compose.qa.yml -p mgqa down -v` la borra.
- **Capturas y auditoría**: `docker build -t mg-e2e e2e` y, p. ej., `docker run --rm --network host -v $PWD/e2e:/e2e/s:ro -v $PWD/e2e/salida:/out -e RUTAS="forecast/garos" -e ANCHOS=390 mg-e2e node /e2e/s/captura.mjs` (errores de consola, desbordes, tiempos, capturas con scroll y toques); `flujo.mjs` recorre ajustes, chat y búsquedas; `ancho.mjs` comprueba solapes de la barra superior; `rendimiento.mjs` mide con red lenta.

## 15. v0.5: lectura de IA a petición, Mi ubicación, actividades y aire
- **La IA ya no se genera sola.** Cada previsión muestra el resumen por reglas y un botón «Generar lectura con IA» (gasta tokens del proveedor elegido). Si ya hay una lectura
  vigente (caché de 3 h por lugar y configuración) se enseña gratis (`/api/forecast/ai?solo_cache=1`). El asistente de chat también es a petición.
- **Mi ubicación** (botón en la lista de Previsión, tarjeta en «Hoy», Ajustes → Cuenta): usa la posición del dispositivo, busca el nombre (Nominatim, con caché) y la comparte con Radio
  (es el mismo QTH `mg-qth`), el asistente («aquí», «donde estoy») y el MCP (`get_my_location`, `get_forecast_here`). Se guarda con precisión de ~100 m en el dispositivo y en la cuenta
  (`user_settings.settings.home`); se borra desde Ajustes. En iPhone, si se deniega: Ajustes → Privacidad y seguridad → Localización.
- **Mejor momento para…** (`/api/activity`): puntúa cada tramo de 3 h con reglas por actividad (temperatura, lluvia, viento, rachas, nubes, humedad, tormenta) y da las 3 mejores ventanas;
  usa los datos ya cacheados (no gasta cuota de Open-Meteo). **Aire, UV y polen** (`/api/air`): Open-Meteo Air Quality (CAMS), caché 1 h.
- **MCP en nombre de la persona**: el middleware verifica el token OAuth, descarta cualquier cabecera `x-mg-*` del cliente y añade la identidad verificada; las herramientas «de cuenta»
  llaman a la API con `X-Admin-Token` + `X-Act-As`. Con la clave fija `MG_MCP_TOKEN` no hay identidad y esas herramientas se niegan. 45 herramientas (ver MAPA-FUNCIONES.md).
- **iPhone en modo app**: la app empieza bajo la barra de estado/isla (`--sat`); si el sistema informa zona segura 0 pero la página llega arriba, se estima por modelo (`pantalla.js`).
  Ajustes → Cuenta → «Diagnóstico de pantalla» muestra lo que ve el dispositivo y la versión. Una versión nueva del service worker recarga la app sola.
