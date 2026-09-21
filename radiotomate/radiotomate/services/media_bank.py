"""Shared media bank paths (Nasgul / MEDIA_ROOT). No Quart."""

from __future__ import annotations

import os
import re
import shutil
import time
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
BANK_TOPS = ("30-habillage", "40-emissions", "50-carts")
CART_DROP_TOP = "50-carts"
FINDER_CARTS = "Carts"
TRASH_TOP = "90-trash"
MAX_BANK_ATTACH = 2000
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
HABILLAGE_TITLES = {
    "jingles": "30-habillage/jingles",
    "jingle": "30-habillage/jingles",
    "ids": "30-habillage/ids",
    "id": "30-habillage/ids",
    "pubs": "30-habillage/pubs",
    "pub": "30-habillage/pubs",
    "beds": "30-habillage/beds",
    "bed": "30-habillage/beds",
    "stings": "30-habillage/stings",
    "sting": "30-habillage/stings",
    "sfx": "30-habillage/sfx",
}


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_ROOT", "/media")).resolve()


def media_writable() -> bool:
    if not os.environ.get("MEDIA_ROOT"):
        return False
    root = media_root()
    if not root.is_dir():
        return False
    try:
        (root / CART_DROP_TOP).mkdir(parents=True, exist_ok=True)
        (root / FINDER_CARTS).mkdir(parents=True, exist_ok=True)
        (root / TRASH_TOP).mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True


def assert_bank_top(rel: str) -> None:
    top = rel.split("/", 1)[0]
    if top in {"00-inbox", TRASH_TOP}:
        raise DomainValidationError("inbox/trash cannot be attached to a cart")
    if top == FINDER_CARTS:
        raise DomainValidationError("Carts/ is a Finder view, not a bank folder")
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


def media_rel(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(media_root()).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None


def _resolved_under_root(raw: str) -> tuple[Path, str]:
    logical = str(raw or "").strip().replace("\\", "/").strip("/")
    if logical.split("/", 1)[0] == FINDER_CARTS:
        raise DomainValidationError("Carts/ is a Finder view, not a bank folder")
    root = media_root()
    candidate = Path(raw)
    path = candidate if candidate.is_absolute() else (root / candidate)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise DomainValidationError("path is outside the media bank") from exc
    if root not in resolved.parents and resolved != root:
        raise DomainValidationError("path is outside the media bank")
    rel = resolved.relative_to(root).as_posix()
    if rel.split("/", 1)[0] == FINDER_CARTS:
        raise DomainValidationError("Carts/ is a Finder view, not a bank folder")
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


def slug_title(title: str) -> str:
    slug = _SLUG_RE.sub("-", (title or "").strip()).strip("-._")
    return slug or "cart"


def finder_name(cart_id: int, title: str) -> str:
    return f"{cart_id}-{slug_title(title)}"


def finder_rel(cart_id: int, title: str) -> str:
    return f"{FINDER_CARTS}/{finder_name(cart_id, title)}"


def habillage_folder_for_title(title: str) -> str | None:
    key = (title or "").strip().lower()
    return HABILLAGE_TITLES.get(key)


def canonical_rel_for_cart(
    cart_id: int, title: str, existing: str | None = None
) -> str:
    text = str(existing or "").strip().replace("\\", "/").strip("/")
    if text:
        try:
            resolved, rel = _resolved_under_root(text)
        except DomainValidationError:
            resolved, rel = None, ""
        else:
            if resolved is not None and resolved.is_dir():
                try:
                    assert_bank_top(rel)
                except DomainValidationError:
                    pass
                else:
                    return rel
    hab = habillage_folder_for_title(title)
    if hab:
        return hab
    return f"{CART_DROP_TOP}/{cart_id}"


def _replace_finder_link(view: Path, real: Path) -> None:
    try:
        if view.is_symlink():
            if view.resolve() == real.resolve():
                return
            view.unlink()
        elif view.exists():
            if view.is_dir() and not any(view.iterdir()):
                view.rmdir()
            else:
                return
        view.symlink_to(real, target_is_directory=True)
    except OSError:
        return


def ensure_cart_tree(cart_id: int, title: str, existing: str | None = None) -> str:
    """Create the canonical bank folder and the Finder Carts/ view. Returns rel."""
    if not media_writable():
        raise DomainValidationError("media bank is not writable")
    rel = canonical_rel_for_cart(cart_id, title, existing)
    root = media_root()
    real = root / rel
    real.mkdir(parents=True, exist_ok=True)
    views = root / FINDER_CARTS
    views.mkdir(parents=True, exist_ok=True)
    wanted = views / finder_name(cart_id, title)
    prefix = f"{cart_id}-"
    for leftover in views.iterdir():
        if (
            leftover.name.startswith(prefix)
            and leftover != wanted
            and leftover.is_symlink()
        ):
            leftover.unlink(missing_ok=True)
    _replace_finder_link(wanted, real)
    exclusive = f"{CART_DROP_TOP}/{cart_id}"
    if rel != exclusive:
        leftover_dir = root / exclusive
        try:
            if leftover_dir.is_dir() and not any(leftover_dir.iterdir()):
                leftover_dir.rmdir()
        except OSError:
            pass
    return rel


def iter_bank_audio(folder: Path) -> list[Path]:
    try:
        folder_res = folder.resolve()
    except (OSError, RuntimeError) as exc:
        raise DomainValidationError("missing folder") from exc
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder_res, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or Path(name).suffix.lower() not in AUDIO_SUFFIXES:
                continue
            path = Path(dirpath) / name
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                continue
            if not resolved.is_file():
                continue
            try:
                resolved.relative_to(folder_res)
            except ValueError:
                continue
            files.append(resolved)
    if len(files) > MAX_BANK_ATTACH:
        raise DomainValidationError(
            f"folder has more than {MAX_BANK_ATTACH} audio files"
        )
    return files


def file_is_stable(path: Path, wait: float = 0.2) -> bool:
    try:
        first = path.stat().st_size
        if first <= 0:
            return False
        time.sleep(wait)
        return path.stat().st_size == first
    except OSError:
        return False


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
            uploads.append({"uploaded_to": resolved, "filename": resolved.name})
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
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
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


def unique_trash_path(name: str) -> Path:
    trash = media_root() / TRASH_TOP
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 1
    while True:
        dest = trash / f"{stem}-{index}{suffix}"
        if not dest.exists():
            return dest
        index += 1


def forget_legacy_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue
        if not resolved.is_file() or path_in_media_bank(resolved):
            continue
        resolved.unlink(missing_ok=True)


def trash_exclusive_file(path: Path, other_refs: int) -> None:
    """Move a bank file to 90-trash when this cart was the last reference."""
    if not path:
        return
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return
    if not resolved.is_file():
        return
    if not path_in_media_bank(resolved):
        resolved.unlink(missing_ok=True)
        return
    if other_refs > 0:
        return
    dest = unique_trash_path(resolved.name)
    try:
        shutil.move(str(resolved), str(dest))
    except OSError:
        return


def owned_by_cart_bank(path: Path, bank_folder: str | None) -> bool:
    rel = media_rel(path)
    bank = str(bank_folder or "").strip().strip("/")
    if not rel or not bank:
        return False
    return rel == bank or rel.startswith(bank + "/")


def bind_cart_bank(cart) -> str | None:
    """Create the canonical folder + Finder view. Stores bank_folder on the cart."""
    mode = getattr(getattr(cart, "mode", None), "value", getattr(cart, "mode", None))
    if mode == "relay" or not getattr(cart, "id", None):
        return getattr(cart, "bank_folder", None)
    if not media_writable():
        return getattr(cart, "bank_folder", None)
    cart.bank_folder = ensure_cart_tree(cart.id, cart.title, cart.bank_folder)
    return cart.bank_folder


def cart_upload_dir(cart) -> Path:
    raw = str(getattr(cart, "bank_folder", None) or "").strip()
    if raw:
        try:
            folder = resolve_bank_dir(raw)
            folder.mkdir(parents=True, exist_ok=True)
            return folder
        except DomainValidationError:
            pass
    path = getattr(cart, "path", None)
    if not path:
        raise DomainValidationError("cart folder is missing")
    return Path(path)


def release_cart_tree(cart_id: int, title: str, bank_folder: str | None) -> None:
    if not os.environ.get("MEDIA_ROOT"):
        return
    root = media_root()
    views = root / FINDER_CARTS
    prefix = f"{cart_id}-"
    if views.is_dir():
        for leftover in views.iterdir():
            if leftover.name.startswith(prefix) and leftover.is_symlink():
                leftover.unlink(missing_ok=True)
    rel = str(bank_folder or "").strip().strip("/")
    exclusive = f"{CART_DROP_TOP}/{cart_id}"
    if rel != exclusive and not rel.startswith(exclusive + "/"):
        return
    folder = root / rel
    if not folder.is_dir() or not path_in_media_bank(folder):
        return
    for dirpath, _dirnames, filenames in os.walk(folder, followlinks=False):
        for name in filenames:
            trash_exclusive_file(Path(dirpath) / name, 0)
    shutil.rmtree(folder, ignore_errors=True)


def copy_into_bank(
    src: Path, dest_dir: Path, sound_id: int | None = None
) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_res = src.resolve()
    except (OSError, RuntimeError):
        return None
    dest = dest_dir / src_res.name
    try:
        if dest.exists():
            dest_res = dest.resolve()
            same_size = (
                dest_res.is_file()
                and dest_res.stat().st_size == src_res.stat().st_size
            )
            if dest_res == src_res or (
                same_size and inspect_audio(dest_res) is not None
            ):
                return dest_res
            tag = sound_id if sound_id is not None else "copy"
            dest = dest_dir / f"{src_res.stem}-{tag}{src_res.suffix}"
        if dest.resolve() == src_res:
            return dest.resolve()
        shutil.copy2(src_res, dest)
    except OSError:
        return None
    if inspect_audio(dest) is None:
        dest.unlink(missing_ok=True)
        return None
    return dest.resolve()


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
