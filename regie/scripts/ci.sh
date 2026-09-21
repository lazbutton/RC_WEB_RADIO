#!/usr/bin/env bash
# CI locale : tests API (Postgres jetable), types et build de l'interface. Lancé avant chaque déploiement.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "== pytest"
.venv/bin/pytest -q -p no:logging
if [[ -d ui ]]; then
  echo "== ui : tsc + build"
  (cd ui && npm run build)
fi
echo "== CI OK"
