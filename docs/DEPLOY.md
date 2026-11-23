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
