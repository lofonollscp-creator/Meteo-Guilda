#!/usr/bin/env bash
# Despliega Meteo Guilda · Radio Guilda en ESTE servidor (radio.guildawork.com). Idempotente: se puede lanzar las veces que haga falta.
#   ./desplegar_produccion.sh                 actualiza desde GitHub (solo avance rápido), reconstruye y verifica
#   ./desplegar_produccion.sh --sin-pull      despliega lo que haya en el directorio (cambios locales, commits aún sin subir)
#   ./desplegar_produccion.sh --probar        antes de desplegar pasa los tests en una copia aislada con una PostGIS desechable
#   ./desplegar_produccion.sh --solo-verificar  no toca nada: solo comprueba que todo responde
# Qué hace: 1) comprueba .env y Docker · 2) git pull --ff-only · 3) (opcional) tests · 4) crea en Hydra el cliente OIDC si falta
#           5) docker compose up -d --build (y reinicia el Caddy interno si cambió deploy/Caddyfile) · 6) verifica salud, OAuth y MCP.
# Debe correr como el usuario que lleva Docker (guilda); si lo lanzas como root se reejecuta como guilda.
# NO toca el Caddy del host (/etc/caddy/sitios.d/radio.caddy): solo avisa si difiere de deploy/Caddyfile.host-snippet.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ "$(id -u)" = 0 ] && id guilda >/dev/null 2>&1; then exec su guilda -c "cd '$PWD' && ./$(basename "$0") $*"; fi

PULL=1; PROBAR=0; SOLO_VERIFICAR=0
for a in "$@"; do case "$a" in
  --sin-pull) PULL=0;; --probar) PROBAR=1;; --solo-verificar) SOLO_VERIFICAR=1;;
  -h|--help) sed -n 2,9p "$0"; exit 0;; *) echo "Opción desconocida: $a" >&2; exit 2;; esac; done

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
aviso(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
fallo(){ printf '  \033[31m✘\033[0m %s\n' "$*" >&2; ERRORES=$((ERRORES+1)); }
parar(){ printf '\033[31mDespliegue detenido:\033[0m %s\n' "$*" >&2; exit 1; }
ERRORES=0
envv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//'; }   # sin comentarios en línea
PUBLICA="$(envv MG_PUBLIC_URL)"; PUBLICA="${PUBLICA:-https://radio.guildawork.com}"
PUERTO="$(envv MG_HTTP_PORT)"; PUERTO="${PUERTO:-8088}"
LOCAL="http://127.0.0.1:$PUERTO"

verificar() {
  echo "== Verificación =="
  local h; h="$(curl -s -m 10 "$LOCAL/api/health")"
  [ "$h" = '{"ok":true,"db":true}' ] && ok "API y base de datos: $h" || fallo "/api/health: '${h:-sin respuesta}'"
  local malos; malos="$(docker compose ps --format '{{.Service}} {{.Status}}' | grep -vE ' Up ' || true)"
  [ -z "$malos" ] && ok "Contenedores en marcha ($(docker compose ps --format '{{.Service}}' | tr '\n' ' '))" || fallo "Contenedores con problemas: $malos"
  local reinicios; reinicios="$(docker compose ps --format '{{.Service}} {{.Status}}' | grep -ci 'restarting' || true)"
  [ "$reinicios" = 0 ] || fallo "Hay contenedores reiniciándose en bucle (docker compose logs)"
  local c; c="$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Host: radio.guildawork.com' "$LOCAL/")"
  [ "$c" = 200 ] && ok "Web estática: 200" || fallo "Web estática: $c"
  if [ -n "$(envv MG_OIDC_ISSUER)" ]; then
    local pr; pr="$(curl -s -m 10 "$LOCAL/.well-known/oauth-protected-resource/mcp")"
    echo "$pr" | grep -q '"resource"' && ok "OAuth: metadatos del recurso OK" || fallo "OAuth: sin metadatos en /.well-known/oauth-protected-resource/mcp"
    curl -s -m 10 "$LOCAL/.well-known/oauth-authorization-server" | grep -q '"registration_endpoint"' && ok "OAuth: metadatos del servidor OK" || fallo "OAuth: sin metadatos del servidor"
    local wa; wa="$(curl -si -m 10 -X POST "$LOCAL/mcp" | tr -d '\r' | grep -i '^www-authenticate' || true)"
    echo "$wa" | grep -q resource_metadata && ok "MCP: responde 401 anunciando el OAuth" || fallo "MCP: el 401 no anuncia resource_metadata"
  else
    local m; m="$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST "$LOCAL/mcp")"; [ "$m" = 401 ] && ok "MCP: 401 sin token" || fallo "MCP sin token: $m (esperado 401)"
  fi
  local rep; rep="$(curl -s -m 20 "$LOCAL/api/repeaters?lat=41.6&lon=1.0&radius_km=60" | head -c 2)"
  [ "$rep" = '[{' ] && ok "Repetidores cargados" || aviso "Sin repetidores todavía (la primera sincronización tarda ~1 min; ver docs/DEPLOY.md §8)"
  if [ -n "$PUBLICA" ] && [ "$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$PUBLICA/api/health")" = 200 ]; then ok "Publicado con TLS: $PUBLICA"; else fallo "$PUBLICA/api/health no responde por HTTPS (¿Caddy del host? ver deploy/Caddyfile.host-snippet)"; fi
  if [ -r /etc/caddy/sitios.d/radio.caddy ]; then
    diff -q <(grep -v '^\s*#' /etc/caddy/sitios.d/radio.caddy) <(grep -v '^\s*#' deploy/Caddyfile.host-snippet) >/dev/null || aviso "El bloque de Caddy del host difiere de deploy/Caddyfile.host-snippet (revísalo si cambiaste el snippet)"
  fi
}

if [ "$SOLO_VERIFICAR" = 1 ]; then verificar; [ "$ERRORES" = 0 ] && echo "Todo bien." || { echo "$ERRORES comprobaciones fallan." >&2; exit 1; }; exit $?; fi

echo "== 1. Requisitos =="
command -v docker >/dev/null && docker compose version >/dev/null 2>&1 || parar "Docker Compose no disponible para $(id -un)"
[ -f .env ] || parar "Falta .env (copia .env.example y rellénalo)"
[ "$(stat -c %a .env)" = 600 ] || { chmod 600 .env; aviso ".env tenía permisos abiertos: puesto a 600"; }
for v in POSTGRES_PASSWORD MG_ADMIN_TOKEN; do [ -n "$(envv $v)" ] || parar "$v vacío en .env"; done
[ -n "$(envv MG_MCP_TOKEN)" ] || [ -n "$(envv MG_OIDC_ISSUER)" ] || parar "Define MG_OIDC_ISSUER (OAuth) o MG_MCP_TOKEN en .env: sin ninguno el MCP no arranca"
[ -n "$(envv OPENROUTER_API_KEY)" ] || aviso "OPENROUTER_API_KEY vacío: se usará el resumen por reglas en lugar de la IA"
ok "Entorno correcto"
ANTES="$(git rev-parse --short HEAD)"

echo "== 2. Código =="
if [ "$PULL" = 1 ]; then
  git diff --quiet && git diff --cached --quiet || parar "Hay cambios sin commitear. Hazles commit o usa --sin-pull."
  if git fetch -q origin 2>/dev/null; then
    UP="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || parar "La rama $(git branch --show-current) no sigue a ninguna rama de GitHub"
    ARR="$(git rev-list --count "HEAD..$UP")"; ADEL="$(git rev-list --count "$UP..HEAD")"
    if [ "$ARR" -gt 0 ] && [ "$ADEL" -gt 0 ]; then parar "Local y GitHub han divergido ($ADEL commits locales sin subir, $ARR nuevos en GitHub). Resuélvelo a mano."; fi
    [ "$ARR" -gt 0 ] && { git merge -q --ff-only "$UP" && ok "Actualizado: $ARR commits nuevos"; } || ok "Sin novedades en GitHub${ADEL:+ ($ADEL commits locales sin subir)}"
  else aviso "No se pudo contactar con GitHub; se despliega lo que hay"; fi
else aviso "--sin-pull: se despliega el directorio tal cual"; fi
git log -1 --format='  commit %h %s' | cut -c1-110

if [ "$PROBAR" = 1 ]; then
  echo "== 3. Tests (copia aislada, base desechable) =="
  T="$(mktemp -d /var/tmp/mg-deploy-test.XXXXXX)"; cp -r backend web "$T"/
  # el contenedor de tests escribe como root dentro de $T: se borra con otro contenedor
  trap 'docker rm -f mg-deploy-test-db >/dev/null 2>&1; docker network rm mg-deploy-test >/dev/null 2>&1; docker run --rm -v "$T:/w" python:3.12-slim sh -c "rm -rf /w/* /w/.[!.]*" >/dev/null 2>&1; rm -rf "$T"' EXIT
  docker network create mg-deploy-test >/dev/null && docker run -d --rm --name mg-deploy-test-db --network mg-deploy-test -e POSTGRES_USER=mg -e POSTGRES_PASSWORD=mg -e POSTGRES_DB=meteoguilda_test postgis/postgis:16-3.4 >/dev/null
  until docker exec mg-deploy-test-db pg_isready -U mg -d meteoguilda_test >/dev/null 2>&1; do sleep 2; done; sleep 4
  docker run --rm --network mg-deploy-test -v "$T:/w" -w /w/backend -e PYTHONDONTWRITEBYTECODE=1 -e MG_DATABASE_URL=postgresql://mg:mg@mg-deploy-test-db:5432/meteoguilda_test python:3.12-slim \
    sh -c "pip install -q -e '.[dev]' >/dev/null 2>&1 && python -m pytest -q" || parar "Los tests fallan: no se despliega"
  ok "Tests en verde"
fi

echo "== 4. Cliente OIDC en Hydra =="
if [ -n "$(envv MG_OIDC_ISSUER)" ]; then
  HYDRA="${MG_HYDRA_ADMIN_URL:-http://127.0.0.1:4445}"; CID="$(envv MG_OIDC_CLIENT_ID)"; CID="${CID:-radio-guilda-web}"
  code="$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$HYDRA/admin/clients/$CID" || true)"
  if [ "$code" = 200 ]; then ok "Cliente $CID ya existe en Hydra"
  elif [ "$code" = 404 ]; then
    curl -sf -m 10 -X POST "$HYDRA/admin/clients" -H 'content-type: application/json' -d "{\"client_id\":\"$CID\",\"client_name\":\"Radio Guilda (MCP)\",\"grant_types\":[\"authorization_code\"],\"response_types\":[\"code\"],\"scope\":\"openid email profile\",\"redirect_uris\":[\"$PUBLICA/api/oauth/callback\"],\"token_endpoint_auth_method\":\"none\",\"skip_consent\":true}" >/dev/null \
      && ok "Cliente $CID creado en Hydra" || parar "No se pudo crear el cliente en Hydra ($HYDRA)"
  else aviso "Hydra admin no responde en $HYDRA (código '${code:-nada}'): crea el cliente $CID a mano (docs/DEPLOY.md §9)"; fi
else aviso "MG_OIDC_ISSUER vacío: el MCP solo acepta la clave fija MG_MCP_TOKEN"; fi

echo "== 5. Contenedores =="
SHA_ANTES="$(sha256sum deploy/Caddyfile | cut -c1-16)"; SHA_PREV="$(cat .caddyfile.sha 2>/dev/null || true)"
docker compose up -d --build 2>&1 | grep -E 'Error|error|Built|Started|Recreated' | tail -8 | sed 's/^/  /'
if [ "$SHA_ANTES" != "$SHA_PREV" ]; then docker compose restart web >/dev/null 2>&1 && ok "Caddy interno reiniciado (cambió deploy/Caddyfile; el reload está desactivado)"; echo "$SHA_ANTES" > .caddyfile.sha; fi
for i in $(seq 1 40); do curl -sf -m 3 "$LOCAL/api/health" >/dev/null 2>&1 && break; sleep 3; done
docker image prune -f >/dev/null 2>&1

verificar
echo
if [ "$ERRORES" = 0 ]; then echo "Despliegue correcto: $(git rev-parse --short HEAD) (antes $ANTES)."; else
  echo "Despliegue con $ERRORES problemas. Para volver atrás (si no tienes commits locales sin subir): git reset --hard $ANTES && ./desplegar_produccion.sh --sin-pull" >&2; exit 1; fi
