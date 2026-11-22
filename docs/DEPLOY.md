# Despliegue en VPS

Requisitos: VPS Linux (Debian/Ubuntu) con **2–4 GB de RAM** y 20 GB de disco, y un dominio apuntando a su IP.

```bash
# 1. Cortafuegos y Docker
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
curl -fsSL https://get.docker.com | sudo sh

# 2. Código y configuración
git clone <repo> meteo-guilda && cd meteo-guilda
cp .env.example .env     # DOMAIN, POSTGRES_PASSWORD, MG_MCP_TOKEN, OPENROUTER_API_KEY…

# 3. Arranque
docker compose up -d --build
docker compose logs -f aprs api
```

Servicios: **db** (PostgreSQL 16 + PostGIS), **caddy** (HTTPS, estáticos, proxy `/api` y `/mcp`), **api** (FastAPI; regenera
los pronósticos de los 6 lugares cada 3 h), **aprs** (ingestor APRS-IS), **mcp** (servidor MCP por HTTP con token).

## Repetidores y cobertura
```bash
docker compose run --rm -v $PWD/repetidores.csv:/tmp/r.csv api python -m meteoguilda.radio import /tmp/r.csv
docker compose run --rm api python -m meteoguilda.radio coverage      # calcula coberturas (usa la API de elevación)
```
Columnas del CSV: `name, callsign, band, freq_mhz, shift_khz, tone_hz, mode, height_m, range_km, lat, lon, source`.
No se incluyen datos de repetidores de serie: impórtalos de una fuente fiable (listas de la URE, radioclubes).

## MCP (conectar IAs)
`MG_MCP_TOKEN` es obligatorio. Con Claude Code:
```bash
claude mcp add --transport http meteoguilda https://TU-DOMINIO/mcp --header "Authorization: Bearer TU_TOKEN"
```
En local (stdio): `MG_API=https://TU-DOMINIO python -m meteoguilda.mcp_server`.

## Operación
- Actualizar: `git pull && docker compose up -d --build`.
- Copia de seguridad de la base de datos: `docker compose exec db pg_dump -U mg meteoguilda | gzip > mg-$(date +%F).sql.gz`.
- Las observaciones APRS se purgan solas a los 7 días.

## Notas de producción
- **Mapa base:** hoy usa los tiles públicos de OpenStreetMap (válido para tráfico bajo). Con uso real, pasar a tiles propios
  (Protomaps/PMTiles en el mismo VPS) o a un proveedor con clave.
- **Rutas:** usa el Valhalla público de FOSSGIS (uso razonable). Para más tráfico, autoalojar Valhalla con el extracto de OSM.
- **Open-Meteo:** el plan gratuito es solo no comercial; ver PLAN.md §2.2.
- **Favoritos y cuaderno QSL** viven en el navegador de cada usuario (no hay cuentas todavía).
