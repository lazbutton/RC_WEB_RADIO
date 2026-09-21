#!/usr/bin/env bash
# Cloud Agent bootstrap for the BUTTON webradio monorepo.
# Idempotent: safe to run repeatedly against a partially prepared workspace.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Installing system packages (build tools, audio codecs for beets, tmux)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq \
  build-essential python3-dev python3-venv \
  ffmpeg lame flac tmux pipx

echo "==> Installing Poetry (user scope)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v poetry >/dev/null 2>&1; then
  pipx install poetry
fi
poetry --version

echo "==> Installing radiotomate Python dependencies"
cd "$REPO_ROOT/radiotomate"
poetry install

# The compiled stylesheet is vendored in the repo. Only rebuild it if missing
# (e.g. a clean checkout without the generated asset) to avoid dirtying the tree.
if [ ! -f "$REPO_ROOT/radiotomate/radiotomate/static/radiotomate.css" ]; then
  echo "==> Building the interface stylesheet (SASS -> static/radiotomate.css)"
  cd "$REPO_ROOT/radiotomate/sass"
  npm install
  npm run package
else
  echo "==> Interface stylesheet already present (skipping SASS build)"
fi

echo "==> Preparing demo data + admin account (idempotent)"
cd "$REPO_ROOT/radiotomate"
if [ ! -f radio_data/radiotomate.db ]; then
  poetry run radiotomate develop --quiet
  # Demo login: button / buttondemo (only created on first initialization).
  poetry run radiotomate -c radio_data/radiotomate.yaml users add button --admin --password buttondemo
fi

echo "==> Installing frontend dependencies (player, console, pads, hub)"
for d in player console pads button/hub; do
  echo "    - $d"
  (cd "$REPO_ROOT/$d" && npm ci)
done

echo "==> Install complete."
