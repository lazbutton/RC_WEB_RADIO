from __future__ import annotations

import imaplib
import logging
import re
import socket
import ssl
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, TypeVar

from imap_tools import MailBox, MailMessage
from imap_tools.utils import encode_folder

from regie.modules.mail.attachments import list_from_message
from regie.modules.mail.classify import CATEGORY_LABELS, excerpt_body
from regie.modules.mail.threads import reply_parent
from regie.modules.mail.ui import date_iso

log = logging.getLogger("inboxzero.imap")

FOLDER_PARENT = "Inbox Zero"
FOLDER_CHILDREN = {
    "todo": "A faire",
    "waiting": "En attente",
    "read": "A lire",
    "newsletters": "Newsletters",
    "spam": "Spam probable",
}
SEEN = "\\Seen"
FLAGGED = "\\Flagged"
DELETED = "\\Deleted"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
FETCH_CHUNK = 40
COPYUID_RE = re.compile(r"COPYUID\s+(\d+)\s+([0-9:,]+)\s+([0-9:,]+)", re.I)
UID_RE = re.compile(r"UID\s+(\d+)", re.I)
FLAGS_RE = re.compile(r"FLAGS\s+\(([^)]*)\)", re.I)
NETWORK_ERRORS = (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, ssl.SSLError, socket.timeout, EOFError)

T = TypeVar("T")


def _header(headers: dict | None, name: str) -> str:
    if not headers:
        return ""
    value = headers.get(name) or headers.get(name.title()) or headers.get(name.lower())
    if isinstance(value, (list, tuple)):
        return str(value[0] if value else "")
    return str(value or "")


def _flat_headers(headers: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if isinstance(value, (list, tuple)):
            out[str(key).lower()] = ", ".join(str(x) for x in value)
        else:
            out[str(key).lower()] = str(value)
    return out


@dataclass
class FetchedMail:
    uid: str
    uidvalidity: str
    message_id: str
    sender: str
    subject: str
    excerpt: str
    headers: dict[str, str]
    mailed_at: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    in_reply_to: str = ""
    seen: bool = True
    flagged: bool = False
    size: int = 0


def build_mail(msg: MailMessage, validity: str, body_chars: int) -> FetchedMail:
    headers = _flat_headers(msg.headers)
    flags = tuple(str(flag) for flag in (getattr(msg, "flags", None) or ()))
    try:
        size = int(getattr(msg, "size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    return FetchedMail(
        uid=str(msg.uid),
        uidvalidity=validity,
        message_id=_header(msg.headers, "message-id"),
        sender=str(msg.from_ or ""),
        subject=str(msg.subject or ""),
        excerpt=excerpt_body(msg.text, msg.html, body_chars),
        headers=headers,
        mailed_at=date_iso(getattr(msg, "date", None)),
        attachments=list_from_message(msg),
        in_reply_to=reply_parent(headers),
        seen=SEEN in flags,
        flagged=FLAGGED in flags,
        size=size,
    )


def since_criteria(days: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    day = (now - timedelta(days=max(0, days))).date()
    return f"SINCE {day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"


def sort_uids(uids: Iterable[str]) -> list[str]:
    def _key(uid: str) -> tuple[int, str]:
        return (int(uid), uid) if uid.isdigit() else (0, uid)

    return sorted({str(uid) for uid in uids if str(uid).strip()}, key=_key, reverse=True)


def expand_uid_set(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            start, _, end = part.partition(":")
            if start.isdigit() and end.isdigit():
                lo, hi = int(start), int(end)
                if lo > hi:
                    lo, hi = hi, lo
                out.extend(str(n) for n in range(lo, hi + 1))
            continue
        out.append(part)
    return out


def parse_copyuid(blobs: Iterable[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for blob in blobs:
        text = blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else str(blob or "")
        for match in COPYUID_RE.finditer(text):
            old = expand_uid_set(match.group(2))
            new = expand_uid_set(match.group(3))
            for src, dst in zip(old, new):
                mapping[src] = dst
    return mapping


def parse_flags_lines(lines: Iterable[Any]) -> dict[str, tuple[bool, bool]]:
    out: dict[str, tuple[bool, bool]] = {}
    for line in lines:
        if line is None:
            continue
        if isinstance(line, tuple):
            line = b" ".join(part for part in line if isinstance(part, (bytes, bytearray)))
        text = line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
        uid = UID_RE.search(text)
        flags = FLAGS_RE.search(text)
        if not uid:
            continue
        raw = flags.group(1) if flags else ""
        out[uid.group(1)] = (SEEN.lower() in raw.lower(), FLAGGED.lower() in raw.lower())
    return out


def _ok(result: tuple, what: str) -> tuple:
    typ = result[0] if result else None
    if typ != "OK":
        detail = ""
        if len(result) > 1 and result[1]:
            first = result[1][0]
            detail = first.decode("utf-8", "replace") if isinstance(first, (bytes, bytearray)) else str(first)
        raise RuntimeError(f"IMAP {what} : {typ or 'sans réponse'} {detail}".strip())
    return result


class MailSession:
    """One long-lived IMAP connection, owned by the job thread."""

    def __init__(self, host: str, port: int, user: str, password: str, timeout: int = 60) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self._box: MailBox | None = None
        self._lock = threading.RLock()
        self.capabilities: set[str] = set()
        self.folders: set[str] = set()
        self.delim = "/"
        self.last_ok: str | None = None
        self.last_error: str | None = None
        self.inbox_status: dict[str, int] = {}

    # --- connexion -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._box is not None

    def _connect(self) -> MailBox:
        box = MailBox(self.host, port=self.port, timeout=self.timeout)
        box.login(self.user, self.password, initial_folder="INBOX")
        self._refresh_capabilities(box)
        self._refresh_folders(box)
        self._box = box
        self.last_error = None
        self.last_ok = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        return box

    def _refresh_capabilities(self, box: MailBox) -> None:
        try:
            typ, data = box.client.capability()
            if typ == "OK" and data and data[0]:
                caps = tuple(str(part).upper() for part in data[0].decode("utf-8", "replace").split())
                box.client.capabilities = caps
        except Exception:
            pass
        self.capabilities = {str(cap).upper() for cap in (box.client.capabilities or ())}

    def _refresh_folders(self, box: MailBox) -> None:
        names: set[str] = set()
        delim = "/"
        try:
            for info in box.folder.list():
                name = getattr(info, "name", "")
                if name:
                    names.add(str(name))
                if getattr(info, "delim", None):
                    delim = str(info.delim)
        except Exception:
            pass
        self.folders = names
        self.delim = delim or "/"

    def box(self) -> MailBox:
        with self._lock:
            if self._box is None:
                return self._connect()
            return self._box

    def close(self) -> None:
        with self._lock:
            box = self._box
            self._box = None
            if box is not None:
                try:
                    box.logout()
                except Exception:
                    pass

    def noop(self) -> bool:
        with self._lock:
            try:
                box = self.box()
                _ok(box.client.noop(), "NOOP")
                self.last_ok = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                return True
            except Exception as exc:
                self.last_error = str(exc)[:300]
                self.close()
                return False

    def run(self, fn: Callable[[MailBox], T], what: str = "IMAP") -> T:
        with self._lock:
            try:
                out = fn(self.box())
            except NETWORK_ERRORS as exc:
                log.warning("%s : %s — reconnexion", what, exc)
                self.close()
                try:
                    out = fn(self.box())
                except Exception as again:
                    self.last_error = str(again)[:300]
                    self.close()
                    raise
            except Exception as exc:
                self.last_error = str(exc)[:300]
                raise
            self.last_ok = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            return out

    # --- lecture ---------------------------------------------------------------

    def folder_names(self) -> tuple[str, dict[str, str]]:
        parent = FOLDER_PARENT
        children = {key: f"{parent}{self.delim}{name}" for key, name in FOLDER_CHILDREN.items()}
        return parent, children

    def status(self, folder: str = "INBOX") -> dict[str, int]:
        def _do(box: MailBox) -> dict[str, int]:
            raw = box.folder.status(folder) or {}
            out: dict[str, int] = {}
            for key, value in raw.items():
                try:
                    out[str(key).upper()] = int(value)
                except (TypeError, ValueError):
                    continue
            return out

        result = self.run(_do, "STATUS")
        if folder == "INBOX":
            self.inbox_status = result
        return result

    def uidvalidity(self) -> str:
        status = self.status("INBOX")
        return str(status.get("UIDVALIDITY") or "0")

    def search_uids(self, criteria: str, folder: str = "INBOX") -> list[str]:
        def _do(box: MailBox) -> list[str]:
            box.folder.set(folder)
            return sort_uids(box.uids(criteria))

        return self.run(_do, "SEARCH")

    def fetch_mails(self, uids: list[str], *, body_chars: int, folder: str = "INBOX", validity: str = "") -> list[FetchedMail]:
        wanted = [str(uid) for uid in uids if str(uid).strip()]
        if not wanted:
            return []
        validity = validity or self.uidvalidity()

        def _do(box: MailBox) -> list[FetchedMail]:
            box.folder.set(folder)
            found: dict[str, FetchedMail] = {}
            for start in range(0, len(wanted), FETCH_CHUNK):
                chunk = wanted[start : start + FETCH_CHUNK]
                for msg in box.fetch(f"UID {','.join(chunk)}", mark_seen=False, bulk=True):
                    mail = build_mail(msg, validity, body_chars)
                    found[mail.uid] = mail
            return [found[uid] for uid in wanted if uid in found]

        return self.run(_do, "FETCH")

    def fetch_flags(self, uids: list[str], folder: str = "INBOX") -> dict[str, tuple[bool, bool]]:
        wanted = [str(uid) for uid in uids if str(uid).strip()]
        if not wanted:
            return {}

        def _do(box: MailBox) -> dict[str, tuple[bool, bool]]:
            box.folder.set(folder)
            out: dict[str, tuple[bool, bool]] = {}
            for start in range(0, len(wanted), 200):
                chunk = wanted[start : start + 200]
                typ, data = box.client.uid("FETCH", ",".join(chunk), "(FLAGS)")
                if typ != "OK":
                    raise RuntimeError("IMAP FETCH FLAGS refusé")
                out.update(parse_flags_lines(data or []))
            return out

        return self.run(_do, "FETCH FLAGS")

    def fetch_message(self, uid: str, folder: str = "INBOX") -> MailMessage | None:
        def _do(box: MailBox) -> MailMessage | None:
            box.folder.set(folder)
            for msg in box.fetch(f"UID {uid}", mark_seen=False, limit=1, bulk=True):
                return msg
            return None

        return self.run(_do, "FETCH message")

    # --- écriture ----------------------------------------------------------------

    def ensure_folders(self) -> dict[str, str]:
        parent, children = self.folder_names()

        def _do(box: MailBox) -> dict[str, str]:
            self._refresh_folders(box)
            if parent not in self.folders:
                box.folder.create(parent)
                self.folders.add(parent)
            for path in children.values():
                if path not in self.folders:
                    box.folder.create(path)
                    self.folders.add(path)
            return children

        return self.run(_do, "CREATE")

    def store_flag(self, uids: list[str], flag: str, value: bool, folder: str = "INBOX") -> None:
        wanted = [str(uid) for uid in uids if str(uid).strip()]
        if not wanted:
            return

        def _do(box: MailBox) -> None:
            box.folder.set(folder)
            for start in range(0, len(wanted), 200):
                chunk = wanted[start : start + 200]
                _ok(
                    box.client.uid("STORE", ",".join(chunk), ("+" if value else "-") + "FLAGS.SILENT", f"({flag})"),
                    "STORE",
                )

        self.run(_do, "STORE")

    def move(self, uids: list[str], destination: str, source: str = "INBOX") -> dict[str, str]:
        """Move messages; returns old uid -> new uid when the server tells us (COPYUID)."""
        wanted = [str(uid) for uid in uids if str(uid).strip()]
        if not wanted:
            return {}

        def _do(box: MailBox) -> dict[str, str]:
            box.folder.set(source)
            mapping: dict[str, str] = {}
            caps = {str(cap).upper() for cap in (box.client.capabilities or ())}
            uidset = ",".join(wanted)
            if "MOVE" in caps:
                typ, data = box.client.uid("MOVE", uidset, encode_folder(destination))
                _ok((typ, data), "MOVE")
                blobs: list[Any] = list(data or [])
                _typ, extra = box.client.response("COPYUID")
                blobs.extend(extra or [])
                mapping.update(parse_copyuid(blobs))
                return mapping
            if "UIDPLUS" not in caps:
                raise RuntimeError("Le serveur IMAP n’a ni MOVE ni UIDPLUS : archivage impossible.")
            typ, data = box.client.uid("COPY", uidset, encode_folder(destination))
            _ok((typ, data), "COPY")
            blobs = list(data or [])
            _typ, extra = box.client.response("COPYUID")
            blobs.extend(extra or [])
            mapping.update(parse_copyuid(blobs))
            _ok(box.client.uid("STORE", uidset, "+FLAGS.SILENT", f"({DELETED})"), "STORE")
            _ok(box.client.uid("EXPUNGE", uidset), "UID EXPUNGE")
            return mapping

        return self.run(_do, "MOVE")

    def find_uid_by_message_id(self, message_id: str, folder: str) -> str:
        mid = (message_id or "").strip()
        if not mid:
            return ""
        safe = mid.replace("\\", "").replace('"', "")

        def _do(box: MailBox) -> str:
            box.folder.set(folder)
            found = sort_uids(box.uids(f'HEADER Message-ID "{safe}"'))
            return found[0] if found else ""

        try:
            return self.run(_do, "SEARCH Message-ID")
        except Exception as exc:
            log.warning("recherche Message-ID : %s", exc)
            return ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "capabilities": sorted(cap for cap in self.capabilities if cap in {"MOVE", "UIDPLUS", "IDLE", "CONDSTORE"}),
            "can_move": "MOVE" in self.capabilities or "UIDPLUS" in self.capabilities,
            "folders": sorted(self.folders)[:60],
            "folder_count": len(self.folders),
            "inbox_messages": self.inbox_status.get("MESSAGES"),
            "inbox_unseen": self.inbox_status.get("UNSEEN"),
        }


# --- compatibilité : helpers sur un MailBox brut -----------------------------------


def connect(host: str, port: int, user: str, password: str) -> MailBox:
    mailbox = MailBox(host, port=port)
    mailbox.login(user, password, initial_folder="INBOX")
    return mailbox


def uidvalidity(mailbox: MailBox) -> str:
    raw = mailbox.folder.status("INBOX") or {}
    value = raw.get("UIDVALIDITY") or raw.get("uidvalidity") or "0"
    return str(value)


def fetch_inbox(mailbox: MailBox, *, limit: int, body_chars: int) -> tuple[str, list[FetchedMail]]:
    validity = uidvalidity(mailbox)
    mailbox.folder.set("INBOX")
    messages = [
        build_mail(msg, validity, body_chars)
        for msg in mailbox.fetch("ALL", mark_seen=False, reverse=True, limit=limit, bulk=FETCH_CHUNK)
    ]
    return validity, messages


def fetch_uid_message(mailbox: MailBox, uid: str, folder: str = "INBOX") -> MailMessage | None:
    try:
        mailbox.folder.set(folder)
        for msg in mailbox.fetch(f"UID {uid}", mark_seen=False, limit=1):
            return msg
    except Exception:
        return None
    return None


def health_probe(host: str, port: int, user: str, password: str) -> dict[str, Any]:
    mailbox = connect(host, port, user, password)
    try:
        folders = sorted(str(getattr(info, "name", "")) for info in mailbox.folder.list())
        status = mailbox.folder.status("INBOX") or {}
        return {
            "ok": True,
            "folder_count": len(folders),
            "inbox_messages": status.get("MESSAGES"),
            "folders": folders[:40],
        }
    finally:
        mailbox.logout()


def label_for(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)
