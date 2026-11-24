#!/usr/bin/env bash
# Lanza el stack de QA (puerto 8099), pasa las comprobaciones de interfaz y lo apaga. Sale con error si alguna falla.
# Uso: ./e2e/correr.sh            (ver docs/DEPLOY.md). Necesita Docker; la imagen mg-e2e se construye sola la primera vez.
set -euo pipefail
cd "$(dirname "$0")/.."
docker image inspect mg-e2e >/dev/null 2>&1 || docker build -t mg-e2e e2e
docker compose -f docker-compose.qa.yml -p mgqa up -d --build
trap 'docker compose -f docker-compose.qa.yml -p mgqa down -v >/dev/null 2>&1' EXIT
for _ in $(seq 1 40); do curl -fs http://127.0.0.1:8099/api/health >/dev/null && break; sleep 2; done
curl -fs http://127.0.0.1:8099/api/health >/dev/null || { echo "El stack de QA no arranca"; exit 1; }
fallos=0
for t in ancho a11y offline; do
  echo "== $t"
  docker run --rm --network host -v "$PWD/e2e/$t.mjs:/e2e/$t.mjs:ro" mg-e2e node "$t.mjs" || fallos=$((fallos + 1))
done
[ "$fallos" -eq 0 ] && echo "Todo correcto" || { echo "$fallos comprobaciones con fallos"; exit 1; }
