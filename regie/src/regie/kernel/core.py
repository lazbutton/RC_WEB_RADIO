from __future__ import annotations

import logging
from typing import Any

from regie.config import Settings
from regie.kernel import db
from regie.kernel.actions import Actions
from regie.kernel.auth import Auth, SecretBox, Secrets
from regie.kernel.connectors import Connectors
from regie.kernel.events import EventBus
from regie.kernel.files import FileService
from regie.kernel.jobs import JobRunner, Scheduler
from regie.kernel.links import Links
from regie.kernel.notifications import Notifications
from regie.kernel.observability import Metrics
from regie.kernel.outbox import Outbox
from regie.kernel.registry import EntityRegistry
from regie.kernel.search import Search

log = logging.getLogger("regie.kernel")


class Kernel:
    """Tout ce qu'un module reçoit. Aucun métier ici."""

    def __init__(self, settings: Settings, *, worker_name: str | None = None) -> None:
        self.settings = settings
        self.dsn = settings.regie_dsn
        self.org_id = self._org_id()
        self.bus = EventBus()
        self.metrics = Metrics()
        self.registry = EntityRegistry()
        self.links = Links(self.dsn, self.org_id)
        self.jobs = JobRunner(self.dsn, self.org_id, self.bus, worker_name=worker_name)
        self.scheduler = Scheduler(self.dsn, self.org_id, self.jobs)
        self.actions = Actions(self.dsn, self.org_id, self.jobs, self.bus)
        self.outbox = Outbox(self.dsn, self.org_id, self.bus)
        self.connectors = Connectors(self.dsn, self.org_id)
        self.search = Search(self.dsn, self.org_id)
        self.notifications = Notifications(
            self.dsn,
            self.org_id,
            self.bus,
            {"public_key": settings.vapid_public_key, "private_key": settings.vapid_private_key, "subject": settings.vapid_subject},
        )
        self.files = FileService(self.dsn, self.org_id, settings.regie_media_root, settings.media_writable)
        self.auth = Auth(self.dsn, self.org_id, settings.regie_session_days)
        self.secrets = Secrets(self.dsn, self.org_id, SecretBox(settings.regie_secret or "dev-only-secret"))
        self.modules: dict[str, Any] = {}
        self._started = False
        self.jobs.register("kernel.housekeeping", self._housekeeping, "maintenance")
        self.jobs.register("kernel.backup", self._backup, "maintenance")

    def _org_id(self) -> str:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT id FROM orgs WHERE slug = %s", (self.settings.regie_org_slug,))
            if not row:
                row = db.fetch_one(conn, "INSERT INTO orgs (slug, name) VALUES (%s, %s) RETURNING id", (self.settings.regie_org_slug, self.settings.regie_org_name))
        assert row is not None
        return str(row["id"])

    # --- réglages simples (clé/valeur) ---------------------------------------------------

    def setting(self, key: str, default: str = "") -> str:
        with db.connect(self.dsn) as conn:
            value = db.scalar(conn, "SELECT value FROM settings WHERE org_id = %s AND key = %s", (self.org_id, key))
        return str(value) if value is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                "INSERT INTO settings (org_id, key, value) VALUES (%s, %s, %s) ON CONFLICT (org_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (self.org_id, key, value),
            )

    def settings_map(self, prefix: str = "") -> dict[str, str]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT key, value FROM settings WHERE org_id = %s AND key LIKE %s", (self.org_id, prefix + "%"))
        return {str(r["key"]): str(r["value"]) for r in rows}

    # --- cycle de vie -----------------------------------------------------------------------

    def sync_registry(self) -> None:
        with db.connect(self.dsn) as conn:
            for kind in self.registry.all():
                db.execute(
                    conn,
                    """
                    INSERT INTO entity_kinds (kind, module, label, label_plural, icon, table_name)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kind) DO UPDATE SET module = EXCLUDED.module, label = EXCLUDED.label, label_plural = EXCLUDED.label_plural, icon = EXCLUDED.icon, table_name = EXCLUDED.table_name, updated_at = now()
                    """,
                    (kind.kind, kind.module, kind.label, kind.label_plural, kind.icon, kind.table),
                )

    def bootstrap(self) -> None:
        """Première mise en route : permissions par défaut, compte admin si l'environnement le demande."""
        self.auth.ensure_default_permissions()
        s = self.settings
        if self.auth.count() == 0 and s.regie_bootstrap_admin_email and s.regie_bootstrap_admin_password:
            self.auth.create_user(s.regie_bootstrap_admin_email, s.regie_bootstrap_admin_name, s.regie_bootstrap_admin_password, "admin")
            log.info("compte admin initial créé pour %s", s.regie_bootstrap_admin_email)
        self.sync_registry()
        self.scheduler.ensure("kernel.housekeeping", 3600, priority=4)
        self.scheduler.ensure("kernel.backup", 24 * 3600, priority=4)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self.settings.regie_run_workers:
            self.jobs.start()
            self.scheduler.start()
        self.outbox.start()

    def stop(self) -> None:
        self.jobs.stop()
        self.scheduler.stop()
        self.outbox.stop()
        for module in self.modules.values():
            stop = getattr(module, "stop", None)
            if stop:
                try:
                    stop()
                except Exception:
                    pass

    def _housekeeping(self, ctx) -> dict[str, Any]:
        del ctx
        pruned_jobs = self.jobs.prune()
        pruned_outbox = self.outbox.prune()
        pruned_sessions = self.auth.prune_sessions()
        return {"jobs": pruned_jobs, "outbox": pruned_outbox, "sessions": pruned_sessions}

    def _backup(self, ctx) -> dict[str, Any]:
        """pg_dump chiffré dans /backups (monté depuis le pool de sauvegarde). Garde 30 fichiers."""
        del ctx
        import shutil
        import subprocess
        from datetime import datetime, timezone
        from pathlib import Path

        dest = Path(self.setting("backup_dir", "/backups"))
        if not shutil.which("pg_dump") or not dest.is_dir():
            return {"skipped": True, "reason": "pg_dump ou dossier absent"}
        proc = subprocess.run(["pg_dump", "--no-owner", "--no-privileges", self.dsn], capture_output=True, timeout=600)
        if proc.returncode != 0:  # jamais le DSN (mot de passe) dans l'erreur
            raise RuntimeError("pg_dump : " + proc.stderr.decode(errors="replace").strip()[-300:])
        dump = proc.stdout
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        target = dest / f"regie-{stamp}.sql.enc"
        target.write_bytes(self.secrets.box.encrypt(dump.decode("utf-8"), aad="backup"))
        for old in sorted(dest.glob("regie-*.sql.enc"))[:-30]:
            old.unlink()
        self.set_setting("last_backup_at", db.iso(db.utcnow()))
        return {"file": str(target), "bytes": target.stat().st_size}

    def backup_age_hours(self) -> float | None:
        raw = self.setting("last_backup_at")
        if not raw:
            return None
        from datetime import datetime

        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return (db.utcnow() - stamp).total_seconds() / 3600

    # --- état ---------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "jobs": self.jobs.snapshot(),
            "connectors": self.connectors.health(),
            "schedules": self.scheduler.list(),
            "search_documents": self.search.count(),
            "media_available": self.files.available,
            "sse_subscribers": self.bus.subscribers,
            "modules": sorted(self.modules),
            "metrics": self.metrics.snapshot(),
            "last_backup_at": self.setting("last_backup_at") or None,
            "backup_age_hours": self.backup_age_hours(),
        }
