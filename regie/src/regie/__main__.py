"""CLI : python -m regie <commande>

  migrate            applique les migrations en attente
  migrate-status     état des migrations
  serve              lance l'API (uvicorn)
  worker LANE[,LANE] travailleur seul (ex. transcribe sur le Mac)
  openapi            écrit le schéma OpenAPI sur la sortie standard
  bootstrap-admin EMAIL NOM   crée un compte admin (mot de passe demandé)
  backup DEST        pg_dump chiffré (nécessite pg_dump et REGIE_SECRET)
  restore-decrypt F  déchiffre une sauvegarde sur la sortie standard
  export DEST        export complet lisible (JSON par table + liste des fichiers NAS)
  import-inboxzero DB.sqlite   reprise des données d'Inbox Zero
  status             résumé de l'état
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from regie.config import get_settings


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    command, rest = args[0], args[1:]
    settings = get_settings()

    if command == "migrate":
        from regie.migrate import migrate

        applied = migrate(settings.regie_dsn)
        print(json.dumps({"applied": applied}, ensure_ascii=False))
        return 0

    if command == "migrate-status":
        from regie.migrate import status

        print(json.dumps(status(settings.regie_dsn), ensure_ascii=False))
        return 0

    if command == "serve":
        import uvicorn

        uvicorn.run("regie.app:create_app", factory=True, host=settings.regie_bind, port=settings.regie_port, log_config=None)
        return 0

    if command == "worker":
        return _worker(rest)

    if command == "openapi":
        from regie.app import create_app

        app = create_app(settings.model_copy(update={"regie_run_workers": False}))
        print(json.dumps(app.openapi(), ensure_ascii=False))
        return 0

    if command == "bootstrap-admin":
        from regie.kernel.core import Kernel

        if len(rest) < 2:
            print("usage : bootstrap-admin EMAIL NOM", file=sys.stderr)
            return 2
        password = os.environ.get("REGIE_ADMIN_PASSWORD") or getpass.getpass("Mot de passe : ")
        kernel = Kernel(settings)
        kernel.auth.ensure_default_permissions()
        user = kernel.auth.create_user(rest[0], rest[1], password, "admin")
        print(json.dumps(user, ensure_ascii=False))
        return 0

    if command == "backup":
        return _backup(rest, settings)

    if command == "restore-decrypt":
        from regie.kernel.auth import SecretBox

        if not rest:
            print("usage : restore-decrypt FICHIER.sql.enc", file=sys.stderr)
            return 2
        box = SecretBox(settings.regie_secret or "dev-only-secret")
        sys.stdout.write(box.decrypt(Path(rest[0]).read_bytes(), aad="backup"))
        return 0

    if command == "status":
        from regie.kernel.core import Kernel

        kernel = Kernel(settings)
        print(json.dumps(kernel.status(), ensure_ascii=False, default=str, indent=2))
        return 0

    if command == "export":
        return _export(rest, settings)

    if command == "import-inboxzero":
        from regie.kernel.core import Kernel
        from regie.modules import load_modules
        from regie.modules.mail.migrate_sqlite import import_sqlite

        if not rest:
            print("usage : import-inboxzero /chemin/inboxzero.db", file=sys.stderr)
            return 2
        kernel = Kernel(settings.model_copy(update={"regie_run_workers": False}))
        load_modules(kernel, ["regie.modules.mail"])
        print(json.dumps(import_sqlite(kernel, rest[0]), ensure_ascii=False))
        return 0

    print(f"commande inconnue : {command}", file=sys.stderr)
    return 2


def _worker(rest: list[str]) -> int:
    """Travailleur autonome : partage la table jobs, ne sert pas l'API. `python -m regie worker transcribe`."""
    import time

    from regie.kernel.core import Kernel
    from regie.kernel.observability import setup_logging
    from regie.modules import load_modules

    settings = get_settings().model_copy(update={"regie_run_workers": False})
    setup_logging(settings.regie_log_json)
    lanes = [part for part in (rest[0] if rest else "").split(",") if part]
    kernel = Kernel(settings, worker_name=f"worker:{os.uname().nodename}")
    load_modules(kernel)
    if lanes:
        kernel.jobs._lanes = {lane: max(1, kernel.jobs._lanes.get(lane, 1)) for lane in lanes}
    kernel.jobs.start()
    print(json.dumps({"worker": kernel.jobs.worker, "lanes": sorted(kernel.jobs._lanes)}))
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        kernel.jobs.stop()
    return 0


def _backup(rest: list[str], settings) -> int:
    """pg_dump | chiffrement AES-GCM (clé dérivée de REGIE_SECRET) → DEST/regie-AAAAMMJJ-HHMM.sql.enc"""
    from regie.kernel.auth import SecretBox

    if not rest:
        print("usage : backup DEST_DIR", file=sys.stderr)
        return 2
    dest = Path(rest[0])
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    proc = subprocess.run(["pg_dump", "--no-owner", "--no-privileges", settings.regie_dsn], capture_output=True)
    if proc.returncode != 0:
        print("pg_dump : " + proc.stderr.decode(errors="replace").strip()[-300:], file=sys.stderr)
        return 1
    dump = proc.stdout
    box = SecretBox(settings.regie_secret or "dev-only-secret")
    target = dest / f"regie-{stamp}.sql.enc"
    target.write_bytes(box.encrypt(dump.decode("utf-8"), aad="backup"))
    for old in sorted(dest.glob("regie-*.sql.enc"))[:-30]:
        old.unlink()
    print(json.dumps({"backup": str(target), "bytes": target.stat().st_size}))
    return 0


def _export(rest: list[str], settings) -> int:
    """Export complet et lisible : un JSON par table (sans secrets ni mots de passe) + la liste des fichiers NAS référencés.

    C'est la garantie de ne jamais être prisonnier : tout ce que Régie sait tient dans un dossier.
    """
    import psycopg
    from psycopg.rows import dict_row

    from regie.kernel.db import dumps

    if not rest:
        print("usage : export DEST_DIR", file=sys.stderr)
        return 2
    dest = Path(rest[0]) / f"regie-export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
    dest.mkdir(parents=True, exist_ok=True)
    skip = {"secrets", "sessions", "auth_events", "schema_migrations", "push_subscriptions"}
    redact = {"users": {"password_hash"}}
    manifest: dict[str, int] = {}
    with psycopg.connect(settings.regie_dsn, row_factory=dict_row) as conn:
        tables = [r["table_name"] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")]
        for table in tables:
            if table in skip:
                continue
            rows = []
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                clean = {k: v for k, v in row.items() if k not in redact.get(table, set())}
                rows.append(clean)
            (dest / f"{table}.json").write_text(dumps(rows), encoding="utf-8")
            manifest[table] = len(rows)
        files = [r["path"] for r in conn.execute("SELECT path FROM files ORDER BY path")]
    (dest / "fichiers-nas.txt").write_text("\n".join(files) + "\n", encoding="utf-8")
    (dest / "MANIFEST.json").write_text(json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "tables": manifest, "files": len(files), "note": "Les octets des fichiers restent sur le NAS (dataset BUTTON-Media). Secrets, sessions et mots de passe ne sont jamais exportés."}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"export": str(dest), "tables": len(manifest), "rows": sum(manifest.values()), "files": len(files)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
