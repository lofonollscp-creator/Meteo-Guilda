# Despliegue en VPS

Requisitos: VPS Linux (Debian/Ubuntu) con 2 GB de RAM y 20 GB de disco, un dominio apuntando a su IP.

```bash
# 1. Cortafuegos y Docker
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable
curl -fsSL https://get.docker.com | sudo sh

# 2. Código y configuración
git clone <repo> meteo-guilda && cd meteo-guilda
cp .env.example .env     # DOMAIN, OPENROUTER_API_KEY

# 3. Arranque
docker compose up -d --build
docker compose logs -f aprs api
```

Servicios: **caddy** (HTTPS, estáticos, proxy `/api`), **api** (FastAPI; regenera los pronósticos de los
6 lugares cada 3 h), **aprs** (ingestor APRS-IS). Datos en el volumen `data`.

Actualizar: `git pull && docker compose up -d --build`.
Copia de seguridad: `docker run --rm -v meteo-guilda_data:/d -v $PWD:/b busybox tar czf /b/data.tgz /d`.

Notas de producción
- **Mapa base:** hoy usa los tiles públicos de OpenStreetMap (válido para tráfico bajo). Con uso real, pasar a
  tiles propios (Protomaps/PMTiles en el mismo VPS) o a un proveedor con clave.
- **Open-Meteo:** el plan gratuito es solo no comercial; ver PLAN.md §2.2.
- **Sin dominio aún:** `DOMAIN=:80` sirve por HTTP en la IP para probar.
