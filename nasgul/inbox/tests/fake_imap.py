from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from inboxzero.imaputil import FLAGGED, FOLDER_CHILDREN, FOLDER_PARENT, SEEN, FetchedMail, sort_uids

UID_RANGE = re.compile(r"UID\s+(\d+):\*", re.I)
MID_RE = re.compile(r'HEADER Message-ID "([^"]+)"', re.I)


@dataclass
class Stored:
    mail: FetchedMail
    message: Any = None
    seen: bool = True
    flagged: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class FakeSession:
    """In-memory stand-in for MailSession: folders, uids, flags, MOVE with COPYUID."""

    def __init__(self, validity: str = "1", caps: tuple[str, ...] = ("MOVE", "UIDPLUS"), fail: set[str] | None = None) -> None:
        self.validity = validity
        self.capabilities = set(caps)
        self.folders: dict[str, dict[str, Stored]] = {"INBOX": {}}
        self.next_uid: dict[str, int] = {"INBOX": 1000}
        self.delim = "/"
        self.calls: list[tuple[Any, ...]] = []
        self.connected = True
        self.last_ok: str | None = "now"
        self.last_error: str | None = None
        self.inbox_status: dict[str, int] = {}
        self.fail = fail or set()

    # --- helpers ---------------------------------------------------------------

    def add(self, mail: FetchedMail, *, message: Any = None, seen: bool = True, flagged: bool = False, folder: str = "INBOX") -> None:
        self.folders.setdefault(folder, {})
        self.folders[folder][str(mail.uid)] = Stored(mail=copy.deepcopy(mail), message=message, seen=seen, flagged=flagged)
        self.next_uid[folder] = max(self.next_uid.get(folder, 1000), int(mail.uid) + 1 if str(mail.uid).isdigit() else 1000)

    def _check(self, op: str) -> None:
        if op in self.fail:
            raise RuntimeError(f"IMAP {op} en panne (test)")

    def folder_names(self) -> tuple[str, dict[str, str]]:
        return FOLDER_PARENT, {key: f"{FOLDER_PARENT}{self.delim}{name}" for key, name in FOLDER_CHILDREN.items()}

    # --- lecture -----------------------------------------------------------------

    def noop(self) -> bool:
        self.calls.append(("noop",))
        return True

    def close(self) -> None:
        self.connected = False

    def status(self, folder: str = "INBOX") -> dict[str, int]:
        box = self.folders.get(folder, {})
        out = {
            "UIDVALIDITY": int(self.validity),
            "MESSAGES": len(box),
            "UNSEEN": sum(1 for stored in box.values() if not stored.seen),
        }
        if folder == "INBOX":
            self.inbox_status = out
        return out

    def uidvalidity(self) -> str:
        return self.validity

    def search_uids(self, criteria: str, folder: str = "INBOX") -> list[str]:
        self._check("search")
        self.calls.append(("search", criteria, folder))
        box = self.folders.get(folder, {})
        uids = list(box)
        match = UID_RANGE.search(criteria or "")
        if match:
            floor = int(match.group(1))
            uids = [uid for uid in uids if uid.isdigit() and int(uid) >= floor] or (sort_uids(box)[:1] if box else [])
        mid = MID_RE.search(criteria or "")
        if mid:
            uids = [uid for uid, stored in box.items() if (stored.mail.message_id or "").strip("<>") == mid.group(1).strip("<>")]
        return sort_uids(uids)

    def fetch_mails(self, uids: list[str], *, body_chars: int = 4000, folder: str = "INBOX", validity: str = "") -> list[FetchedMail]:
        self._check("fetch")
        self.calls.append(("fetch", tuple(uids), folder))
        box = self.folders.get(folder, {})
        out: list[FetchedMail] = []
        for uid in uids:
            stored = box.get(str(uid))
            if not stored:
                continue
            mail = copy.deepcopy(stored.mail)
            mail.uid = str(uid)
            mail.uidvalidity = validity or self.validity
            mail.seen = stored.seen
            mail.flagged = stored.flagged
            out.append(mail)
        return out

    def fetch_flags(self, uids: list[str], folder: str = "INBOX") -> dict[str, tuple[bool, bool]]:
        self._check("flags")
        box = self.folders.get(folder, {})
        return {str(uid): (box[str(uid)].seen, box[str(uid)].flagged) for uid in uids if str(uid) in box}

    def fetch_message(self, uid: str, folder: str = "INBOX") -> Any:
        self._check("message")
        self.calls.append(("message", uid, folder))
        stored = self.folders.get(folder, {}).get(str(uid))
        return stored.message if stored else None

    # --- écriture -------------------------------------------------------------------

    def ensure_folders(self) -> dict[str, str]:
        parent, children = self.folder_names()
        self.folders.setdefault(parent, {})
        for path in children.values():
            self.folders.setdefault(path, {})
            self.next_uid.setdefault(path, 1)
        return children

    def store_flag(self, uids: list[str], flag: str, value: bool, folder: str = "INBOX") -> None:
        self._check("store")
        self.calls.append(("store", tuple(uids), flag, value, folder))
        box = self.folders.get(folder, {})
        for uid in uids:
            stored = box.get(str(uid))
            if not stored:
                raise RuntimeError(f"uid {uid} absent de {folder}")
            if flag == SEEN:
                stored.seen = value
            elif flag == FLAGGED:
                stored.flagged = value

    def move(self, uids: list[str], destination: str, source: str = "INBOX") -> dict[str, str]:
        self._check("move")
        self.calls.append(("move", tuple(uids), destination, source))
        if "MOVE" not in self.capabilities and "UIDPLUS" not in self.capabilities:
            raise RuntimeError("Le serveur IMAP n’a ni MOVE ni UIDPLUS : archivage impossible.")
        src = self.folders.setdefault(source, {})
        dst = self.folders.setdefault(destination, {})
        mapping: dict[str, str] = {}
        for uid in uids:
            stored = src.pop(str(uid), None)
            if stored is None:
                raise RuntimeError(f"uid {uid} absent de {source}")
            new_uid = str(self.next_uid.get(destination, 1))
            self.next_uid[destination] = int(new_uid) + 1
            dst[new_uid] = stored
            if "UIDPLUS" in self.capabilities:
                mapping[str(uid)] = new_uid
        return mapping

    def find_uid_by_message_id(self, message_id: str, folder: str) -> str:
        mid = (message_id or "").strip().strip("<>")
        if not mid:
            return ""
        for uid, stored in self.folders.get(folder, {}).items():
            if (stored.mail.message_id or "").strip("<>") == mid:
                return uid
        return ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "capabilities": sorted(self.capabilities),
            "can_move": "MOVE" in self.capabilities or "UIDPLUS" in self.capabilities,
            "folders": sorted(self.folders),
            "folder_count": len(self.folders),
            "inbox_messages": len(self.folders.get("INBOX", {})),
            "inbox_unseen": sum(1 for stored in self.folders.get("INBOX", {}).values() if not stored.seen),
        }


def make_mail(uid: str, subject: str = "Sujet", sender: str = "Ada <ada@test>", excerpt: str = "Corps du mail.", message_id: str = "", **extra: Any) -> FetchedMail:
    return FetchedMail(
        uid=str(uid),
        uidvalidity="1",
        message_id=message_id or f"<m{uid}@test>",
        sender=sender,
        subject=subject,
        excerpt=excerpt,
        headers={},
        mailed_at=extra.pop("mailed_at", "2026-09-20T10:00:00+00:00"),
        attachments=extra.pop("attachments", []),
        in_reply_to=extra.pop("in_reply_to", ""),
        seen=extra.pop("seen", True),
        flagged=extra.pop("flagged", False),
    )
