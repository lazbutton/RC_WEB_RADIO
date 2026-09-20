"""Shared media bank paths (Nasgul / MEDIA_ROOT). No Quart."""

from __future__ import annotations

import os
from pathlib import Path

import mutagen

from radiotomate.domain.errors import DomainValidationError

AUDIO_SUFFIXES = {
    ".mp3",
    ".wav",
    ".wave",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".m4a",
    ".aac",
    ".aiff",
    ".aif",
    ".wma",
    ".mp2",
    ".webm",
}
BANK_TOPS = ("30-habillage", "40-emissions")
MAX_BANK_ATTACH = 2000


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_ROOT", "/media")).resolve()


def assert_bank_top(rel: str) -> None:
    top = rel.split("/", 1)[0]
    if top in {"00-inbox", "90-trash"}:
        raise DomainValidationError("inbox/trash cannot be attached to a cart")
    if top not in BANK_TOPS:
        raise DomainValidationError(
            "rotation and archives belong to auto-DJ, not carts"
        )


def path_in_media_bank(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = media_root()
        return resolved == root or root in resolved.parents
    except (OSError, RuntimeError):
        return False


def _resolved_under_root(raw: str) -> tuple[Path, str]:
    root = media_root()
    candidate = Path(raw)
    path = candidate if candidate.is_absolute() else (root / candidate)
    resolved = path.resolve()
    if root not in resolved.parents and resolved != root:
        raise DomainValidationError("path is outside the media bank")
    rel = resolved.relative_to(root).as_posix()
    return resolved, rel


def resolve_bank_path(raw: str) -> Path:
    resolved, rel = _resolved_under_root(raw)
    assert_bank_top(rel)
    if not resolved.is_file():
        raise DomainValidationError(f"missing file: {rel}")
    return resolved


def resolve_bank_dir(raw: str) -> Path:
    resolved, rel = _resolved_under_root(raw)
    if resolved == media_root():
        raise DomainValidationError("pick a media folder")
    assert_bank_top(rel)
    if not resolved.is_dir():
        raise DomainValidationError(f"missing folder: {rel}")
    return resolved


def normalize_bank_folder(raw: str | None) -> str | None:
    text = str(raw or "").strip().replace("\\", "/").strip("/")
    if not text:
        return None
    folder = resolve_bank_dir(text)
    return folder.relative_to(media_root()).as_posix()


def iter_bank_audio(folder: Path) -> list[Path]:
    files = [
        path
        for path in sorted(folder.rglob("*"))
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in AUDIO_SUFFIXES
    ]
    if len(files) > MAX_BANK_ATTACH:
        raise DomainValidationError(
            f"folder has more than {MAX_BANK_ATTACH} audio files"
        )
    return files


def collect_bank_uploads(raws: list) -> list[dict]:
    seen: set[Path] = set()
    uploads: list[dict] = []
    for raw in raws:
        folder = resolve_bank_dir(str(raw))
        for path in iter_bank_audio(folder):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            uploads.append({"uploaded_to": path, "filename": path.name})
    if len(uploads) > MAX_BANK_ATTACH:
        raise DomainValidationError(
            f"folder has more than {MAX_BANK_ATTACH} audio files"
        )
    return uploads


def list_bank_folders(root: Path) -> list[dict]:
    folders: list[dict] = []
    for top in BANK_TOPS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            count = sum(
                1
                for name in filenames
                if not name.startswith(".")
                and Path(name).suffix.lower() in AUDIO_SUFFIXES
            )
            if not count:
                continue
            rel = Path(dirpath).resolve().relative_to(root).as_posix()
            folders.append({"path": rel, "count": count})
            if len(folders) >= 200:
                return folders
    return folders


def _looks_like_audio(head: bytes) -> bool:
    if len(head) < 12:
        return False
    if head.startswith((b"ID3", b"OggS", b"fLaC")):
        return True
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return True
    if head.startswith(b"FORM") and head[8:12] in {b"AIFF", b"AIFC"}:
        return True
    if head[4:8] == b"ftyp":
        return True
    return head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


def audio_length(path: Path) -> float | None:
    try:
        metadata = mutagen.File(path)
    except Exception:
        return None
    length = getattr(getattr(metadata, "info", None), "length", None)
    if length is None:
        return None
    return float(length)


def inspect_audio(path: Path) -> float | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError:
        return None
    if not _looks_like_audio(head):
        return None
    return audio_length(path)
