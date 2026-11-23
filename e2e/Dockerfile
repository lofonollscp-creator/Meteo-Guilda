# Navegador sin cabeza para revisar la web (capturas, desbordes, errores de consola, tiempos de carga).
FROM node:22-bookworm
WORKDIR /e2e
RUN npm init -y >/dev/null && npm i --no-audit --no-fund playwright && npx playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
ENV NODE_PATH=/e2e/node_modules
