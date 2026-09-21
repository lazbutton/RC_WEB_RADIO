#!/usr/bin/env bash
# Régénère openapi.json (Postgres jetable) puis les types TypeScript de l'interface. À lancer après tout changement d'API.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
.venv/bin/python - <<'EOF'
import json, os, sys
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
from conftest import TempCluster
from regie import migrate as m
import psycopg
cluster = TempCluster(); cluster.start()
try:
    with psycopg.connect(cluster.admin_dsn, autocommit=True) as conn:
        conn.execute("CREATE DATABASE regie_openapi")
    dsn = cluster.dsn("regie_openapi"); m.migrate(dsn)
    os.environ.update(REGIE_DSN=dsn, REGIE_RUN_WORKERS="0", REGIE_SECRET="x" * 32, REGIE_LOG_JSON="0")
    from regie.config import get_settings; get_settings.cache_clear()
    from regie.app import create_app
    spec = create_app(get_settings(), start_workers=False).openapi()
    json.dump(spec, open("openapi.json", "w"), ensure_ascii=False, indent=1)
    print("routes :", len(spec["paths"]))
finally:
    cluster.stop()
EOF
(cd ui && npm run types)
echo "== types générés : ui/src/api/types.gen.ts"
