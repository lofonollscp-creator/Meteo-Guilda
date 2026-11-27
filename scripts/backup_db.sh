#!/usr/bin/env bash
# Copia lógica de la base de datos de Meteo-Guilda (volumen pgdata: verificación de modelos, dispositivos push, cachés, cuentas).
#   scripts/backup_db.sh           volcado (pg_dump -Fc) con rotación y resumen en $DESTINO/estado.json
#   scripts/backup_db.sh --probar  restaura la última copia en una base TEMPORAL del mismo servidor, cuenta tablas y filas y la borra
# Variables: MG_BACKUPS_DIR (por defecto /var/backups/meteo-guilda; lo ideal es otro disco), MG_BACKUPS_CONSERVAR (14 copias).
# Las copias llevan datos de personas (dispositivos push, cuentas): carpeta 700 y ficheros 600; si se sacan del servidor, solo cifradas (Restic).
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO" || exit 2
DESTINO="${MG_BACKUPS_DIR:-/var/backups/meteo-guilda}"; CONSERVAR="${MG_BACKUPS_CONSERVAR:-14}"
PSQL=(docker compose exec -T db psql -U mg -v ON_ERROR_STOP=1 -q -At)
umask 077; mkdir -p "$DESTINO" && chmod 700 "$DESTINO" || { echo "No se puede crear $DESTINO" >&2; exit 2; }
fallo() { echo "FALLO: $*" >&2; printf '{"ok":false,"hora":"%s","error":"%s"}\n' "$(date -u +%FT%TZ)" "$*" > "$DESTINO/estado.json"; exit 1; }

if [ "${1:-}" = "--probar" ]; then
  ULT="$(ls -1t "$DESTINO"/meteoguilda-*.dump 2>/dev/null | head -1)"; [ -n "$ULT" ] || fallo "no hay copias que probar"
  T="mg_restauracion_prueba"
  "${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $T" -c "CREATE DATABASE $T" >/dev/null || fallo "no se pudo crear la base temporal"
  # Las extensiones (postgis) las crea la propia copia; los avisos de «ya existe» no son errores.
  docker compose exec -T db pg_restore -U mg -d "$T" --no-owner --exit-on-error < "$ULT" >/dev/null 2>"$DESTINO/.restore.err" \
    || { "${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $T" >/dev/null; fallo "pg_restore falló: $(head -c 200 "$DESTINO/.restore.err" | tr '\n"' '  ')"; }
  TABLAS="$("${PSQL[@]}" -d "$T" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
  FILAS="$("${PSQL[@]}" -d "$T" -c "SELECT coalesce(sum(n_live_tup),0)::bigint FROM pg_stat_user_tables")"
  ORIG="$("${PSQL[@]}" -d meteoguilda -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
  "${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS $T" >/dev/null; rm -f "$DESTINO/.restore.err"
  [ "$TABLAS" = "$ORIG" ] && [ "$TABLAS" -gt 0 ] || fallo "la restauración tiene $TABLAS tablas y el original $ORIG"
  echo "Restauración de prueba correcta: $(basename "$ULT") → $TABLAS tablas (≈ $FILAS filas); base temporal borrada."; exit 0
fi

F="$DESTINO/meteoguilda-$(date -u +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U mg -d meteoguilda -Fc > "$F.tmp" 2>"$DESTINO/.dump.err" || { rm -f "$F.tmp"; fallo "pg_dump falló: $(head -c 200 "$DESTINO/.dump.err" | tr '\n"' '  ')"; }
[ -s "$F.tmp" ] || { rm -f "$F.tmp"; fallo "copia vacía"; }
mv "$F.tmp" "$F"; rm -f "$DESTINO/.dump.err"
ls -1t "$DESTINO"/meteoguilda-*.dump | tail -n +"$((CONSERVAR + 1))" | xargs -r rm -f
N="$(ls -1 "$DESTINO"/meteoguilda-*.dump | wc -l)"
printf '{"ok":true,"hora":"%s","fichero":"%s","bytes":%s,"copias":%s}\n' "$(date -u +%FT%TZ)" "$(basename "$F")" "$(stat -c %s "$F")" "$N" > "$DESTINO/estado.json"
echo "Copia hecha: $F ($(du -h "$F" | cut -f1)); $N copias guardadas (máx. $CONSERVAR)."
