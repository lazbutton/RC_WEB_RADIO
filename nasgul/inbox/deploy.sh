#!/usr/bin/env bash
# Rebuild Inbox Zero on Nasgul (même image :30128, vrais mails).
# Depuis le Mac : ./nasgul/inbox/deploy.sh
# Depuis Cursor SSH sur Nasgul : ./nasgul/inbox/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/nasgul/inbox"
DEST="/mnt/data/apps/inboxzero/app"
HOST="${NASGUL_HOST:-nasgul}"   # NASGUL_HOST=nasgul-ts hors LAN (Tailscale)
SSH=(ssh -o BatchMode=yes)

RSYNC_EXCLUDES=(
  --exclude '.venv'
  --exclude 'node_modules'
  --exclude 'dist'
  --exclude '__pycache__'
  --exclude '.pytest_cache'
  --exclude '*.pyc'
)

if [[ ! -f "$SRC/Dockerfile" ]]; then
  echo "Dockerfile introuvable dans $SRC" >&2
  exit 1
fi

build_and_redeploy() {
  sudo rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$1/" "$DEST/"
  sudo docker build -t nasgul-inboxzero:local "$DEST"
  sudo midclt call --job app.redeploy inboxzero
}

if [[ -d /mnt/data/apps/inboxzero ]]; then
  build_and_redeploy "$SRC"
else
  rsync -az --delete "${RSYNC_EXCLUDES[@]}" -e 'ssh -o BatchMode=yes' \
    "$SRC/" "$HOST:/tmp/inboxzero-src/"
  "${SSH[@]}" "$HOST" 'bash -s' <<'EOS'
set -euo pipefail
sudo rsync -a --delete --exclude '.venv' --exclude 'node_modules' --exclude 'dist' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '*.pyc' \
  /tmp/inboxzero-src/ /mnt/data/apps/inboxzero/app/
sudo docker build -t nasgul-inboxzero:local /mnt/data/apps/inboxzero/app
sudo midclt call --job app.redeploy inboxzero
sudo rm -rf /tmp/inboxzero-src
EOS
fi
