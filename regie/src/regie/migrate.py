"""Migrations SQL numérotées (`migrations/NNNN_nom.sql`), appliquées une fois, dans l'ordre, chacune dans sa transaction.

Règle : une migration n'est jamais modifiée après déploiement ; on en ajoute une autre.
Compatibilité : une migration doit laisser la version précédente du code fonctionner (ajout, puis bascule, puis nettoyage).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import psycopg

log = logging.getLogger("regie.migrate")
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    out: list[tuple[int, str, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = NAME_RE.match(path.name)
        if not match:
            raise ValueError(f"nom de migration invalide : {path.name}")
        out.append((int(match.group(1)), path.name, path))
    numbers = [n for n, _, _ in out]
    if len(numbers) != len(set(numbers)):
        raise ValueError("deux migrations portent le même numéro")
    return out


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version integer PRIMARY KEY,
          name text NOT NULL,
          checksum text NOT NULL,
          applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def applied(conn: psycopg.Connection) -> dict[int, dict]:
    _ensure_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")
        return {int(row[0]): {"name": row[1], "checksum": row[2]} for row in cur.fetchall()}


def pending(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    done = applied(conn)
    out = []
    for version, name, path in list_migrations(directory):
        if version in done:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            if done[version]["checksum"] != checksum:
                raise RuntimeError(f"migration {name} modifiée après application")
            continue
        out.append((version, name, path))
    return out


def migrate(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the names applied."""
    names: list[str] = []
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_lock(7245001)")
        try:
            for version, name, path in pending(conn, directory):
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
                log.info("migration %s", name)
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, name, checksum) VALUES (%s, %s, %s)",
                        (version, name, checksum),
                    )
                names.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(7245001)")
    return names


def status(dsn: str, directory: Path = MIGRATIONS_DIR) -> dict:
    with psycopg.connect(dsn) as conn:
        done = applied(conn)
        todo = pending(conn, directory)
    return {"applied": sorted(done), "pending": [name for _, name, _ in todo]}
