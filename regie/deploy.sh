#!/usr/bin/env bash
# Déploiement Régie sur Nasgul : sauvegarde → build → migration → démarrage → fumée → retour arrière si échec.
# Depuis le Mac : ./regie/deploy.sh            (NASGUL_HOST=nasgul-ts hors LAN)
# Première fois : créer /mnt/data/apps/regie/secrets.env (voir secrets.env.example).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${NASGUL_HOST:-nasgul}"
APP_DIR="/mnt/data/apps/regie"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "$ROOT/regie/Dockerfile" ]]; then
  echo "Dockerfile introuvable" >&2
  exit 1
fi

echo "== rsync vers $HOST"
ssh -o BatchMode=yes "$HOST" 'mkdir -p /tmp/regie-src/regie /tmp/regie-src/button'
rsync -az --delete \
  --exclude '.venv' --exclude 'node_modules' --exclude 'dist' --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  -e 'ssh -o BatchMode=yes' \
  "$ROOT/regie/" "$HOST:/tmp/regie-src/regie/"
rsync -az -e 'ssh -o BatchMode=yes' "$ROOT/button/brand.json" "$HOST:/tmp/regie-src/button/brand.json"

ssh -o BatchMode=yes "$HOST" "STAMP=$STAMP bash -s" <<'EOS'
set -euo pipefail
APP_DIR=/mnt/data/apps/regie
if ! sudo zfs list data/apps/regie >/dev/null 2>&1; then sudo zfs create data/apps/regie; fi
if ! sudo zfs list backup/regie >/dev/null 2>&1; then sudo zfs create backup/regie; fi
sudo mkdir -p "$APP_DIR/app" "$APP_DIR/data" "$APP_DIR/pg" /mnt/backup/regie
sudo chown 999:999 "$APP_DIR/pg"
sudo chown 568:568 "$APP_DIR/data" /mnt/backup/regie
export NASGUL_TS_IP="$(ip -4 -o addr show tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
export NASGUL_TS_IP="${NASGUL_TS_IP:-127.0.0.1}"
if [[ ! -f "$APP_DIR/secrets.env" ]]; then
  echo "secrets.env manquant dans $APP_DIR (voir secrets.env.example)" >&2
  exit 1
fi
sudo rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude 'dist' /tmp/regie-src/regie/ "$APP_DIR/app/regie/"
sudo mkdir -p "$APP_DIR/app/button" && sudo cp /tmp/regie-src/button/brand.json "$APP_DIR/app/button/brand.json"
sudo cp "$APP_DIR/app/regie/compose.yml" "$APP_DIR/compose.yml"
cd "$APP_DIR"
sudo chown "$(id -u)" "$APP_DIR/secrets.env" && sudo chmod 600 "$APP_DIR/secrets.env"
set -a; source "$APP_DIR/secrets.env"; set +a

# 1. Sauvegarde si la base tourne déjà.
if sudo docker ps --format '{{.Names}}' | grep -q '^regie-regie-pg'; then
  echo "== sauvegarde avant migration"
  sudo docker exec regie-regie-pg-1 pg_dump -U regie --no-owner regie | gzip | sudo tee "/mnt/backup/regie/pre-deploy-$STAMP.sql.gz" >/dev/null
  ls -t /mnt/backup/regie/pre-deploy-*.sql.gz 2>/dev/null | tail -n +8 | xargs -r sudo rm -f
fi

# 2. Image : la précédente reste disponible pour le retour arrière.
if sudo docker image inspect nasgul-regie:local >/dev/null 2>&1; then
  sudo docker tag nasgul-regie:local nasgul-regie:previous
fi
echo "== build"
sudo docker build -t nasgul-regie:local -f "$APP_DIR/app/regie/Dockerfile" "$APP_DIR/app"

# 3. Postgres puis migration dans un conteneur éphémère.
sudo -E docker compose --env-file "$APP_DIR/secrets.env" -p regie -f "$APP_DIR/compose.yml" up -d regie-pg
echo "== attente de Postgres"
for i in $(seq 1 40); do
  state="$(sudo docker inspect --format '{{.State.Health.Status}}' regie-regie-pg-1 2>/dev/null || echo starting)"
  [[ "$state" == "healthy" ]] && break
  sleep 2
done
[[ "$state" == "healthy" ]] || { echo "Postgres ne démarre pas" >&2; sudo docker logs --tail=40 regie-regie-pg-1 >&2; exit 1; }
echo "== migration"
sudo -E docker compose --env-file "$APP_DIR/secrets.env" -p regie -f "$APP_DIR/compose.yml" run -T --rm --no-deps regie python -m regie migrate </dev/null

# 4. Démarrage et fumée.
sudo -E docker compose --env-file "$APP_DIR/secrets.env" -p regie -f "$APP_DIR/compose.yml" up -d regie
ok=0
for i in $(seq 1 30); do
  sleep 2
  if curl -fsS http://127.0.0.1:30130/health | grep -q '"ok": *true'; then ok=1; break; fi
done
if [[ "$ok" == "1" ]] && [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:30130/api/v1/me)" == "401" ]]; then
  echo "== fumée OK"
  sudo docker image prune -f >/dev/null 2>&1 || true
else
  echo "== fumée KO : retour à l'image précédente" >&2
  sudo -E docker compose --env-file "$APP_DIR/secrets.env" -p regie -f "$APP_DIR/compose.yml" logs --tail=60 regie >&2 || true
  if sudo docker image inspect nasgul-regie:previous >/dev/null 2>&1; then
    sudo docker tag nasgul-regie:previous nasgul-regie:local
    sudo -E docker compose --env-file "$APP_DIR/secrets.env" -p regie -f "$APP_DIR/compose.yml" up -d regie
  fi
  exit 1
fi
sudo rm -rf /tmp/regie-src
EOS
echo "== déployé : http://nasgul.taild4714f.ts.net:30130"
