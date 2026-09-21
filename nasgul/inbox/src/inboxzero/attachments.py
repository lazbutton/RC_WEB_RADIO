from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

PDF_MAX = 20 * 1024 * 1024
IMAGE_MAX = 8 * 1024 * 1024
AUDIO_MAX = 30 * 1024 * 1024
CACHE_MAX = 150 * 1024 * 1024
PDF_TEXT_MAX = 800
APIC_MAX = 2 * 1024 * 1024
AUDIO_MAX_SECONDS = 30 * 60
INLINE_IMAGE_SKIP = 80_000

AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".aiff", ".aif"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DANGER_EXT = {".exe", ".bat", ".cmd", ".com", ".scr", ".js", ".msi", ".dll", ".sh"}
KIND_LIMIT = {"pdf": PDF_MAX, "image": IMAGE_MAX, "audio": AUDIO_MAX}

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

AUDIO_SNIFF = {"mp3", "wav", "flac", "ogg", "m4a", "aiff"}
IMAGE_SNIFF = {"jpeg", "png", "webp"}
KIND_SNIFF = {
    "pdf": {"pdf"},
    "image": IMAGE_SNIFF,
    "audio": AUDIO_SNIFF,
}


def parse_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def dump_list(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def listed(raw: Any) -> bool:
    if raw is None:
        return False
    return str(raw).strip() != ""


def names(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        name = str(row.get("filename") or "").strip()
        if name:
            out.append(name[:120])
    return out[:12]


def safe_filename(name: str) -> str:
    base = Path(name or "piece").name
    cleaned = SAFE_NAME.sub("_", base).strip("._") or "piece"
    return cleaned[:80]


def _suffixes(name: str) -> list[str]:
    return [part.lower() for part in Path(name or "").suffixes]


def kind_of(filename: str, content_type: str) -> str:
    mime = (content_type or "").split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    if ext in PDF_EXT or mime == "application/pdf":
        return "pdf"
    if ext in AUDIO_EXT or mime.startswith("audio/"):
        return "audio"
    if ext in IMAGE_EXT or mime in {"image/jpeg", "image/png", "image/webp", "image/jpg"}:
        return "image"
    if mime.startswith("image/") and "svg" not in mime:
        return "other"
    return "other"


def sniff(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        return "pdf"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"OggS"):
        return "ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"
    if data.startswith(b"FORM") and data[8:12] == b"AIFF":
        return "aiff"
    if data.startswith(b"MZ"):
        return "exe"
    if data.startswith(b"\x7fELF"):
        return "elf"
    if data.startswith(b"\xca\xfe\xba\xbe") or data.startswith(b"\xcf\xfa\xed\xfe"):
        return "macho"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    stripped = data.lstrip()[:32].lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "html"
    if data.startswith(b"#!"):
        return "script"
    if data.startswith(b"<svg") or b"<svg" in data[:200].lower():
        return "svg"
    return "unknown"


def _double_danger(filename: str) -> bool:
    parts = _suffixes(filename)
    if len(parts) >= 2 and parts[-1] in DANGER_EXT:
        return True
    return Path(filename or "").suffix.lower() in DANGER_EXT


def _audio_ok(payload: bytes, filename: str) -> str | None:
    bio = BytesIO(payload)
    bio.name = safe_filename(filename)
    try:
        parsed = MutagenFile(bio)
    except Exception:
        return "pas un fichier audio"
    if parsed is None or parsed.info is None:
        return "pas un fichier audio"
    length = float(getattr(parsed.info, "length", 0) or 0)
    if length <= 0 or length > AUDIO_MAX_SECONDS:
        return "durée audio anormale"
    tags = getattr(parsed, "tags", None)
    if tags is not None:
        for key in list(tags.keys()):
            if str(key).startswith("APIC"):
                data = getattr(tags[key], "data", b"") or b""
                if len(data) > APIC_MAX:
                    return "pochette ID3 trop lourde"
    return None


def _pdf_text(payload: bytes) -> tuple[str, bool]:
    scripted = b"/JavaScript" in payload or b"/JS" in payload
    try:
        from pypdf import PdfReader
    except Exception:
        return "", scripted
    try:
        reader = PdfReader(BytesIO(payload))
    except Exception:
        return "", scripted
    chunks: list[str] = []
    for page in reader.pages[:6]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue
    text = re.sub(r"\s+", " ", " ".join(chunks)).strip()[:PDF_TEXT_MAX]
    return text, scripted


def inspect(payload: bytes, filename: str, content_type: str) -> dict[str, Any]:
    kind = kind_of(filename, content_type)
    if kind == "other":
        return {"ok": False, "kind": kind, "error": "type de pièce non pris en charge"}
    if _double_danger(filename):
        return {"ok": False, "kind": kind, "error": "extension dangereuse"}
    limit = KIND_LIMIT[kind]
    if len(payload) > limit:
        return {"ok": False, "kind": kind, "error": "fichier trop lourd"}
    if not payload:
        return {"ok": False, "kind": kind, "error": "fichier vide"}
    magic = sniff(payload)
    if magic in {"exe", "elf", "macho"}:
        return {
            "ok": False,
            "kind": kind,
            "error": "Ce n’est pas un son, en-tête d’exécutable." if kind == "audio" else "fichier refusé (en-tête suspect)",
        }
    if magic in {"zip", "html", "script", "svg"}:
        return {"ok": False, "kind": kind, "error": "fichier refusé (archive ou script)"}
    allowed = KIND_SNIFF[kind]
    if magic not in allowed:
        if kind == "audio":
            return {"ok": False, "kind": kind, "error": "pas un fichier audio"}
        return {"ok": False, "kind": kind, "error": "contenu et nom ne correspondent pas"}
    text = ""
    note = ""
    if kind == "audio":
        problem = _audio_ok(payload, filename)
        if problem:
            return {"ok": False, "kind": kind, "error": problem}
    if kind == "pdf":
        text, scripted = _pdf_text(payload)
        if scripted:
            note = "PDF avec script, texte seulement"
    mime = {
        "pdf": "application/pdf",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "aiff": "audio/aiff",
    }.get(magic, content_type or "application/octet-stream")
    return {"ok": True, "kind": kind, "mime": mime, "text": text, "note": note, "error": ""}


def meta_from_imap(att: Any, index: int) -> dict[str, Any] | None:
    filename = str(getattr(att, "filename", None) or f"piece-{index}")
    content_type = str(getattr(att, "content_type", None) or "")
    disposition = str(getattr(att, "content_disposition", None) or "").lower()
    payload = getattr(att, "payload", None)
    size = int(getattr(att, "size", None) or (len(payload) if payload else 0))
    kind = kind_of(filename, content_type)
    cid = bool(getattr(att, "content_id", None))
    if kind == "image" and ("inline" in disposition or cid) and size and size < INLINE_IMAGE_SKIP:
        return None
    return {
        "n": index,
        "filename": filename[:120],
        "content_type": content_type[:120],
        "size": size,
        "kind": kind,
        "status": "listed",
        "error": "",
        "text": "",
        "note": "",
    }


def list_from_message(msg: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index = 0
    for att in getattr(msg, "attachments", None) or []:
        meta = meta_from_imap(att, index)
        if meta is None:
            continue
        rows.append(meta)
        index += 1
    return rows[:20]


def payload_for_n(msg: Any, n: int) -> bytes:
    index = 0
    for att in getattr(msg, "attachments", None) or []:
        meta = meta_from_imap(att, index)
        if meta is None:
            continue
        if index == n:
            return bytes(getattr(att, "payload", None) or b"")
        index += 1
    raise KeyError("pièce introuvable")


def public_rows(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in parse_list(raw):
        out.append(
            {
                "n": int(row.get("n", 0)),
                "filename": str(row.get("filename") or "")[:120],
                "content_type": str(row.get("content_type") or "")[:120],
                "size": int(row.get("size") or 0),
                "kind": str(row.get("kind") or "other"),
                "status": str(row.get("status") or "listed"),
                "error": str(row.get("error") or ""),
                "note": str(row.get("note") or ""),
                "text": str(row.get("text") or "")[:PDF_TEXT_MAX],
            }
        )
    return out


def append_excerpt(summary: str, text: str) -> str:
    blob = (text or "").strip()
    current = (summary or "").rstrip()
    if not blob:
        return current
    if blob in current:
        return current
    return f"{current}\n\n{blob}".strip()[:1200]


def encode_list(value: Any) -> str:
    if isinstance(value, list):
        return dump_list(value)
    return str(value or "")


def cache_dir(data_dir: str) -> Path:
    path = Path(data_dir) / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(data_dir: str, item_id: int, n: int, filename: str) -> Path:
    folder = cache_dir(data_dir) / str(item_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{n}-{safe_filename(filename)}"


def prune_cache(data_dir: str, limit: int = CACHE_MAX) -> None:
    root = cache_dir(data_dir)
    files = [p for p in root.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if total <= limit:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for path in files:
        if total <= limit:
            break
        size = path.stat().st_size
        try:
            path.unlink()
            total -= size
        except OSError:
            continue


def upsert_row(rows: list[dict[str, Any]], n: int, **fields: Any) -> list[dict[str, Any]]:
    out = [dict(row) for row in rows]
    for row in out:
        if int(row.get("n", -1)) == n:
            row.update(fields)
            return out
    extra = {"n": n, "filename": "", "kind": "other", "status": "listed"}
    extra.update(fields)
    out.append(extra)
    return out
