from __future__ import annotations

import re
from typing import Any

from regie.modules.mail.attachments import names, parse_list

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
FILE_RE = re.compile(r"\b[\w.\-]+\.(?:pdf|docx?|xlsx?|pptx?|png|jpe?g|gif|zip|mp3|wav|txt)\b", re.I)
NUM_RE = re.compile(r"\b\d{2,}\b")


def _facts(blob: str) -> set[str]:
    text = blob or ""
    out: set[str] = set()
    out.update(m.group(0).lower() for m in IP_RE.finditer(text))
    out.update(m.group(0).lower() for m in EMAIL_RE.finditer(text))
    out.update(m.group(0).lower() for m in FILE_RE.finditer(text))
    out.update(m.group(0) for m in NUM_RE.finditer(text))
    return out


def source_blob(item: dict[str, Any], extra: str = "") -> str:
    files = names(parse_list(item.get("attachments")))
    parts = [
        extra,
        str(item.get("subject") or ""),
        str(item.get("sender") or ""),
        str(item.get("excerpt") or ""),
        str(item.get("summary") or ""),
        " ".join(files),
        str(item.get("message_id") or ""),
        str(item.get("in_reply_to") or ""),
    ]
    return "\n".join(parts)


def check_draft(draft: str, source: str, signature: str = "") -> str | None:
    body = (draft or "").strip()
    if not body:
        return None
    allowed = f"{source or ''}\n{signature or ''}".lower()
    for fact in sorted(_facts(body), key=len, reverse=True):
        needle = fact.lower()
        if needle in allowed:
            continue
        return fact
    return None
