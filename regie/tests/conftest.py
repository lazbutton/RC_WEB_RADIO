"""Un cluster Postgres jetable par session de tests (initdb + pg_ctl), une base par test clonée depuis un modèle migré.

`REGIE_TEST_DSN` permet d'utiliser un serveur existant à la place (CI, Nasgul).
"""

from __future__ import annotations

import itertools
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

from regie import migrate as migrate_mod

BIN_DIRS = [
    "/opt/homebrew/opt/postgresql@17/bin",
    "/opt/homebrew/opt/postgresql@16/bin",
    "/opt/homebrew/opt/postgresql@18/bin",
    "/usr/lib/postgresql/17/bin",
    "/usr/lib/postgresql/16/bin",
    "/usr/local/pgsql/bin",
]
_counter = itertools.count(1)


def _find(binary: str) -> str:
    for directory in BIN_DIRS:
        candidate = Path(directory) / binary
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(binary)
    if not found:
        raise RuntimeError(f"{binary} introuvable : installer postgresql ou définir REGIE_TEST_DSN")
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TempCluster:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="regie-pg-"))
        self.data = self.dir / "data"
        self.port = _free_port()
        self.admin_dsn = f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    def start(self) -> None:
        subprocess.run([_find("initdb"), "-D", str(self.data), "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--locale=C"], check=True, capture_output=True)
        conf = self.data / "postgresql.conf"
        conf.write_text(conf.read_text() + f"\nport = {self.port}\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = '{self.dir}'\nfsync = off\nsynchronous_commit = off\nfull_page_writes = off\nmax_connections = 200\n")
        subprocess.run([_find("pg_ctl"), "-D", str(self.data), "-l", str(self.dir / "pg.log"), "-w", "start"], check=True, capture_output=True)
        for _ in range(50):
            try:
                psycopg.connect(self.admin_dsn, connect_timeout=2).close()
                return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("Postgres temporaire ne répond pas")

    def stop(self) -> None:
        subprocess.run([_find("pg_ctl"), "-D", str(self.data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def dsn(self, name: str) -> str:
        return f"postgresql://postgres@127.0.0.1:{self.port}/{name}"


@pytest.fixture(scope="session")
def cluster() -> Iterator[TempCluster | None]:
    if os.environ.get("REGIE_TEST_DSN"):
        yield None
        return
    temp = TempCluster()
    temp.start()
    try:
        yield temp
    finally:
        temp.stop()


@pytest.fixture(scope="session")
def template_dsn(cluster: TempCluster | None) -> str:
    """Base modèle migrée une fois ; chaque test la clone (CREATE DATABASE … TEMPLATE)."""
    if cluster is None:
        base = os.environ["REGIE_TEST_DSN"]
        migrate_mod.migrate(base)
        return base
    with psycopg.connect(cluster.admin_dsn, autocommit=True) as conn:
        conn.execute("CREATE DATABASE regie_tpl")
    dsn = cluster.dsn("regie_tpl")
    migrate_mod.migrate(dsn)
    return dsn


@pytest.fixture
def dsn(cluster: TempCluster | None, template_dsn: str, monkeypatch) -> Iterator[str]:
    from regie.config import get_settings
    from regie.kernel import db

    if cluster is None:
        target = template_dsn
    else:
        name = f"regie_t{next(_counter)}"
        with psycopg.connect(cluster.admin_dsn, autocommit=True) as conn:
            conn.execute(f"CREATE DATABASE {name} TEMPLATE regie_tpl")
        target = cluster.dsn(name)
    monkeypatch.setenv("REGIE_DSN", target)
    monkeypatch.setenv("REGIE_SECRET", "test-secret-test-secret")
    monkeypatch.setenv("REGIE_RUN_WORKERS", "0")
    monkeypatch.setenv("REGIE_LOG_JSON", "0")
    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        yield target
    finally:
        db.close_pools()
        get_settings.cache_clear()
        if cluster is not None:
            with psycopg.connect(cluster.admin_dsn, autocommit=True) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "media"
    for folder in ("00-inbox", "10-rotation", "20-archives", "30-habillage", "40-emissions", "50-carts", "90-trash"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv("REGIE_MEDIA_ROOT", str(root))
    monkeypatch.setenv("REGIE_DATA", str(tmp_path / "data"))
    from regie.config import get_settings

    get_settings.cache_clear()
    return root


@pytest.fixture
def kernel(dsn: str, media_root: Path):
    from regie.config import get_settings
    from regie.kernel.core import Kernel

    settings = get_settings()
    k = Kernel(settings)
    k.auth.ensure_default_permissions()
    return k


@pytest.fixture
def app_client(dsn: str, media_root: Path):
    """Application complète (modules chargés, travailleurs arrêtés) avec un admin connecté."""
    from fastapi.testclient import TestClient

    from regie.app import create_app
    from regie.config import get_settings

    settings = get_settings()
    app = create_app(settings, start_workers=False)
    kernel = app.state.kernel
    kernel.auth.ensure_default_permissions()
    kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
    client = TestClient(app, headers={"X-Regie": "1"})
    client.__enter__()
    res = client.post("/api/v1/auth/login", json={"email": "laz@test", "password": "motdepasse-solide"})
    assert res.status_code == 200, res.text
    client.kernel = kernel  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        client.__exit__(None, None, None)
