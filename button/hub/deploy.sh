#!/usr/bin/env bash
# Hub BUTTON :30120 — SPA React, image nginx (dist/ seulement).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/button/hub"
DEST="/mnt/data/apps/button-hub/app"

cp "$ROOT/button/brand.json" "$SRC/brand.json"
(cd "$SRC" && npm ci && npm run build)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/dist"
cp -R "$SRC/dist/." "$STAGE/dist/"
cp "$SRC/nginx.conf" "$SRC/Dockerfile" "$SRC/compose.yml" "$SRC/brand.json" "$STAGE/"

bring_up() {
  sudo mkdir -p "$DEST"
  sudo rsync -a --delete "$1/" "$DEST/"
  sudo docker build -t nasgul-button-hub:local "$DEST"
  if sudo midclt call app.query '[["id", "=", "button-hub"]]' 2>/dev/null | grep -q button-hub; then
    sudo midclt call --job app.redeploy button-hub
  else
    (cd "$DEST" && sudo docker compose up -d)
  fi
}

if [[ -d /mnt/data/apps ]]; then
  bring_up "$STAGE"
else
  rsync -az --delete -e 'ssh -o BatchMode=yes' "$STAGE/" nasgul-ts:/tmp/button-hub-src/
  ssh -o BatchMode=yes nasgul-ts 'bash -s' <<'EOS'
set -euo pipefail
sudo mkdir -p /mnt/data/apps/button-hub/app
sudo rsync -a --delete /tmp/button-hub-src/ /mnt/data/apps/button-hub/app/
sudo docker build -t nasgul-button-hub:local /mnt/data/apps/button-hub/app
if sudo midclt call app.query '[["id", "=", "button-hub"]]' | grep -q button-hub; then
  sudo midclt call --job app.redeploy button-hub
else
  cd /mnt/data/apps/button-hub/app
  sudo docker compose up -d
fi
sudo rm -rf /tmp/button-hub-src
EOS
fi
