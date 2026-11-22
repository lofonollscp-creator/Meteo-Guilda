# Despliegue en el VPS de Hetzner (compartido) · radio.guildawork.com

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
**nginx**: `deploy/nginx-radio.guildawork.com.conf` (incluye certbot). **Caddy del host**: `deploy/Caddyfile.host-snippet`.
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
- **MCP**: `claude mcp add --transport http guilda https://radio.guildawork.com/mcp --header "Authorization: Bearer $MG_MCP_TOKEN"`.
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
