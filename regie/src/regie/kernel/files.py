from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from regie.kernel import db

AUDIO = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".aiff", ".aif"}
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DOCS = {".pdf", ".docx", ".doc", ".odt", ".txt", ".md"}
TRANSCRIPT = {".json", ".srt", ".whisper"}


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in AUDIO:
        return "audio"
    if ext in IMAGE:
        return "image"
    if ext in DOCS:
        return "document"
    if ext in TRANSCRIPT:
        return "transcript"
    return "other"


class FileService:
    """Accès au dataset de la médiathèque : lecture partout sous la racine, écriture sur liste blanche, jamais de suppression."""

    def __init__(self, dsn: str, org_id: str, root: str, writable: list[str]) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.root = Path(root).resolve()
        self.writable = [w.strip("/") for w in writable if w.strip("/")]

    @property
    def available(self) -> bool:
        return self.root.is_dir()

    def resolve(self, rel: str) -> Path:
        candidate = (self.root / (rel or "").strip("/")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("chemin hors de la médiathèque") from exc
        return candidate

    def rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root)).replace(os.sep, "/")

    def can_write(self, rel: str) -> bool:
        clean = (rel or "").strip("/")
        return any(clean == w or clean.startswith(w + "/") for w in self.writable)

    def list(self, rel: str = "") -> dict[str, Any]:
        base = self.resolve(rel)
        if not base.is_dir():
            raise FileNotFoundError(rel)
        dirs: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": self.rel(entry), "writable": self.can_write(self.rel(entry))})
            else:
                files.append(self.describe(entry, stat))
        return {"path": self.rel(base) if base != self.root else "", "writable": self.can_write(self.rel(base)) if base != self.root else False, "dirs": dirs, "files": files}

    def describe(self, path: Path, stat: os.stat_result | None = None) -> dict[str, Any]:
        stat = stat or path.stat()
        return {
            "name": path.name,
            "path": self.rel(path),
            "size": stat.st_size,
            "kind": kind_of(path.name),
            "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat(),
        }

    def stat(self, rel: str) -> dict[str, Any]:
        path = self.resolve(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return self.describe(path)

    def open_path(self, rel: str) -> Path:
        path = self.resolve(rel)
        if not path.is_file():
            raise FileNotFoundError(rel)
        return path

    def write_bytes(self, rel: str, data: bytes, overwrite: bool = False) -> dict[str, Any]:
        if not self.can_write(rel):
            raise PermissionError("dossier en lecture seule pour Régie")
        path = self.resolve(rel)
        if path.exists() and not overwrite:
            raise FileExistsError(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return self.index_file(rel)

    def write_text(self, rel: str, text: str, overwrite: bool = True) -> dict[str, Any]:
        return self.write_bytes(rel, text.encode("utf-8"), overwrite=overwrite)

    def sha256(self, rel: str, limit: int = 64 * 1024 * 1024) -> str:
        path = self.open_path(rel)
        if path.stat().st_size > limit:
            h = hashlib.sha256()
            with path.open("rb") as fh:
                h.update(fh.read(1024 * 1024))
                fh.seek(-1024 * 1024, os.SEEK_END)
                h.update(fh.read(1024 * 1024))
            h.update(str(path.stat().st_size).encode())
            return "partial:" + h.hexdigest()
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def index_file(self, rel: str, meta: dict | None = None) -> dict[str, Any]:
        info = self.stat(rel)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                INSERT INTO files (org_id, path, name, size, mime, kind, meta, modified_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (path) DO UPDATE SET size = EXCLUDED.size, mime = EXCLUDED.mime, kind = EXCLUDED.kind,
                  meta = files.meta || EXCLUDED.meta, modified_at = EXCLUDED.modified_at
                RETURNING *
                """,
                (self.org_id, info["path"], info["name"], info["size"], info["mime"], info["kind"], db.J(meta or {}), info["modified_at"]),
            )
        return db.jsonable(row) or info

    def indexed(self, rel: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM files WHERE path = %s", (rel.strip("/"),))
        return db.jsonable(row)

    def scan(self, rel: str = "", kinds: set[str] | None = None, limit: int = 5000) -> int:
        """Indexe récursivement un dossier (tâche de fond)."""
        base = self.resolve(rel)
        count = 0
        for path in base.rglob("*"):
            if count >= limit:
                break
            if not path.is_file() or path.name.startswith(".") or path.suffix == ".part":
                continue
            if kinds and kind_of(path.name) not in kinds:
                continue
            self.index_file(self.rel(path))
            count += 1
        return count
