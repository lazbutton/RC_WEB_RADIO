#!/usr/bin/env bash
# Exercice de restauration : rejoue la dernière sauvegarde chiffrée dans un Postgres jetable sur le Mac.
# Usage : REGIE_SECRET=… ./regie/scripts/restore-drill.sh [fichier.sql.enc]   (par défaut : dernier fichier récupéré depuis Nasgul)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${NASGUL_HOST:-nasgul-ts}"
WORK="$(mktemp -d /tmp/regie-restore-XXXX)"
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@17/bin}"
PORT=54329
PSQL="${PSQL:-$([ -x /opt/homebrew/opt/libpq/bin/psql ] && echo /opt/homebrew/opt/libpq/bin/psql || echo "$PSQL")}"  # psql >= 18 pour \restrict
trap 'set +e; "$PGBIN/pg_ctl" -D "$WORK/pg" -m immediate stop >/dev/null 2>&1; rm -rf "$WORK"' EXIT

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  LATEST="$(ssh -o BatchMode=yes "$HOST" 'ls -t /mnt/backup/regie/regie-*.sql.enc 2>/dev/null | head -1')"
  [[ -n "$LATEST" ]] || { echo "aucune sauvegarde chiffrée sur $HOST" >&2; exit 1; }
  scp -q -o BatchMode=yes "$HOST:$LATEST" "$WORK/backup.sql.enc"
  FILE="$WORK/backup.sql.enc"
fi

echo "== déchiffrement"
PYTHONPATH="$ROOT/regie/src" "$ROOT/regie/.venv/bin/python" -m regie restore-decrypt "$FILE" > "$WORK/backup.sql"

echo "== Postgres jetable :$PORT"
"$PGBIN/initdb" -D "$WORK/pg" -U postgres --auth=trust >/dev/null
"$PGBIN/pg_ctl" -D "$WORK/pg" -o "-p $PORT -k $WORK -c listen_addresses=127.0.0.1" -l "$WORK/pg.log" -w start >/dev/null
"$PSQL" -q -h 127.0.0.1 -p "$PORT" -U postgres -c "CREATE DATABASE regie" postgres
"$PSQL" -q -h 127.0.0.1 -p "$PORT" -U postgres -d regie -v ON_ERROR_STOP=1 -f "$WORK/backup.sql" >/dev/null

echo "== vérification"
REGIE_DSN="postgresql://postgres@127.0.0.1:$PORT/regie" PYTHONPATH="$ROOT/regie/src" "$ROOT/regie/.venv/bin/python" -m regie migrate-status
"$PSQL" -h 127.0.0.1 -p "$PORT" -U postgres -d regie -At -c "SELECT 'users=' || count(*) FROM users UNION ALL SELECT 'actions=' || count(*) FROM actions UNION ALL SELECT 'jobs=' || count(*) FROM jobs"
echo "== restauration OK"
