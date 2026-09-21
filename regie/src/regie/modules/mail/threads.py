from __future__ import annotations

import re
from typing import Any

PREFIX = re.compile(r"^(re|fw|fwd|tr|enc|aw|sv)\s*:\s*", re.I)
SPACES = re.compile(r"\s+")
HEADER_LINE = re.compile(
    r"^(from|sent|to|cc|bcc|subject|date|de|envoyé|envoye|à|a|objet)\s*:",
    re.I,
)
HEADER_VALUE = re.compile(
    r"^(from|sent|to|cc|bcc|subject|date|de|envoyé|envoye|à|a|objet)\s*:\s*(.+)$",
    re.I,
)
FWD_SUBJECT = re.compile(r"^(fw|fwd|tr|enc)\s*:", re.I)
REPLY_PREFIX = re.compile(r"^(re|aw|sv)\s*:\s*", re.I)
PARTICIPANT_KEYS = {
    "from": "De",
    "de": "De",
    "to": "À",
    "à": "À",
    "a": "À",
    "cc": "Cc",
    "bcc": "Cci",
}
SEPARATORS = (
    re.compile(
        r"(?:^|[\s>])Le\s+\d{1,2}\s+\S+\.?\s+\d{4}.+?\ba écrit\s*:",
        re.I | re.S,
    ),
    re.compile(
        r"(?:^|[\s>])On\s+(?:mon|tue|wed|thu|fri|sat|sun|\d{1,2})\b.+?\bwrote\s*:",
        re.I | re.S,
    ),
    re.compile(r"-{2,}\s*Original Message\s*-{0,}", re.I),
    re.compile(r"-{2,}\s*Message d['’]origine\s*-{0,}", re.I),
    re.compile(r"-{5,}\s*Forwarded message\s*-{0,}", re.I),
    re.compile(r"Début du message transféré", re.I),
    re.compile(r"(?:^|\n)From:\s+\S.+\s+Sent:\s+", re.I | re.S),
    re.compile(r"(?:^|\s)From:\s+\S.+?\s+Sent:\s+", re.I),
    re.compile(r"(?:^|\n)De\s*:\s+\S.+\s+Envoyé\s*:", re.I | re.S),
    re.compile(r"(?:^|\s)De\s*:\s+\S.+?\s+Envoyé\s*:", re.I),
    re.compile(r"_{8,}"),
)


def _cut_quotes(text: str) -> str:
    cuts = [m.start() for rx in SEPARATORS if (m := rx.search(text))]
    if not cuts:
        return text
    return text[: min(cuts)].rstrip(" \t>")


def _tidy_lines(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    kept: list[str] = []
    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if HEADER_LINE.match(stripped):
            continue
        if stripped == "--":
            break
        kept.append(re.sub(r"[ \t]+", " ", line).rstrip())
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _useful(text: str) -> bool:
    blob = SPACES.sub(" ", text).strip()
    if len(blob) < 8:
        return False
    if HEADER_LINE.match(blob):
        return False
    return True


def unescape_newlines(text: str) -> str:
    """Some senders (calendar bots) ship literal backslash-n sequences."""
    if "\\n" not in text:
        return text
    return text.replace("\\r\\n", "\n").replace("\\n", "\n")


def visible_body(raw: str | None) -> str:
    original = unescape_newlines((raw or "").strip())
    if not original:
        return ""
    peeled = _tidy_lines(_cut_quotes(original))
    if _useful(peeled):
        return peeled
    quoted = _tidy_lines(original)
    if _useful(quoted):
        return quoted
    parts = [part.strip() for part in re.split(r"\n\s*\n", quoted or original) if _useful(part)]
    return parts[0] if parts else SPACES.sub(" ", original).strip()[:500]


def looks_forwarded(subject: str = "", raw: str = "") -> bool:
    subj = (subject or "").strip()
    stripped = subj
    while True:
        nxt = REPLY_PREFIX.sub("", stripped, count=1).strip()
        if nxt == stripped:
            break
        stripped = nxt
    if FWD_SUBJECT.match(subj) or FWD_SUBJECT.match(stripped):
        return True
    text = raw or ""
    if re.search(r"idée invitée", text, re.I):
        return True
    markers = (
        SEPARATORS[2],
        SEPARATORS[3],
        SEPARATORS[4],
        SEPARATORS[5],
        SEPARATORS[6],
        SEPARATORS[8],
    )
    return any(rx.search(text) for rx in markers)


def _layers(raw: str) -> tuple[str, str]:
    original = (raw or "").strip()
    if not original:
        return "", ""
    cuts = [m.start() for rx in SEPARATORS if (m := rx.search(original))]
    if not cuts:
        return original, ""
    at = min(cuts)
    return original[:at].rstrip(" \t>"), original[at:].strip()


def _participants(quoted: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in quoted.replace("\r\n", "\n").split("\n")[:50]:
        match = HEADER_VALUE.match(raw.strip().lstrip("> "))
        if not match:
            continue
        label = PARTICIPANT_KEYS.get(match.group(1).lower())
        if not label:
            continue
        value = match.group(2).strip()
        if not value:
            continue
        line = f"{label}: {value}"
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(line)
        if len(found) >= 8:
            break
    return found


def forward_context(raw: str | None, subject: str = "", limit: int = 2000) -> dict[str, Any]:
    original = (raw or "").strip()
    note, quoted = _layers(original)
    note = _tidy_lines(note)
    if quoted:
        inner = _tidy_lines(quoted)
        if not _useful(inner):
            inner = SPACES.sub(" ", quoted).strip()
    else:
        inner = ""
    if inner:
        inner = inner[:limit]
    return {
        "note": note,
        "quoted": inner,
        "participants": _participants(quoted),
        "forwarded": bool(quoted) or looks_forwarded(subject, original),
    }


def weak_summary(summary: str, excerpt: str) -> bool:
    recap = SPACES.sub(" ", (summary or "").strip())
    if len(recap) < 24:
        return True
    body = SPACES.sub(" ", (excerpt or "").strip())
    if not body:
        return False
    if recap == SPACES.sub(" ", short_summary(excerpt)):
        return True
    head = body[:80]
    if head and recap.startswith(head[: min(40, len(head))]):
        return True
    if body.startswith(recap[: min(80, len(recap))]) and len(recap) < 220:
        return True
    return False


def item_needs_brief(item: dict[str, Any]) -> bool:
    category = item.get("category") or ""
    if category in {"newsletters", "spam"}:
        return False
    if (item.get("status") or "proposed") != "proposed":
        return False
    raw = item.get("excerpt") or ""
    subject = item.get("subject") or ""
    if category not in {"todo", "waiting"} and not looks_forwarded(subject, raw):
        return False
    return weak_summary(item.get("summary") or "", raw)


def thread_key(subject: str) -> str:
    text = (subject or "").strip()
    while True:
        nxt = PREFIX.sub("", text, count=1).strip()
        if nxt == text:
            break
        text = nxt
    return SPACES.sub(" ", text).casefold()


def thread_title(subject: str) -> str:
    text = (subject or "").strip() or "(sans objet)"
    while True:
        nxt = PREFIX.sub("", text, count=1).strip()
        if nxt == text:
            break
        text = nxt
    return text or "(sans objet)"


def norm_mid(value: str) -> str:
    return re.sub(r"[<>\s]", "", value or "").casefold()


def reply_parent(headers: dict[str, str] | None = None, in_reply_to: str = "") -> str:
    if in_reply_to:
        token = in_reply_to.split()[0].strip()
        return token
    headers = headers or {}
    irt = (headers.get("in-reply-to") or "").strip()
    if irt:
        return irt.split()[0].strip()
    refs = (headers.get("references") or "").split()
    if refs:
        return refs[-1].strip()
    return ""


def cluster_groups(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not items:
        return []
    parent = {int(item["id"]): int(item["id"]) for item in items}

    def find(xid: int) -> int:
        while parent[xid] != xid:
            parent[xid] = parent[parent[xid]]
            xid = parent[xid]
        return xid

    def union(left: int, right: int) -> None:
        root_l, root_r = find(left), find(right)
        if root_l != root_r:
            parent[root_r] = root_l

    by_mid: dict[str, int] = {}
    by_sub: dict[str, int] = {}
    for item in items:
        iid = int(item["id"])
        mid = norm_mid(str(item.get("message_id") or ""))
        if mid:
            if mid in by_mid:
                union(iid, by_mid[mid])
            by_mid[mid] = iid
        irt = norm_mid(str(item.get("in_reply_to") or ""))
        if irt:
            if irt in by_mid:
                union(iid, by_mid[irt])
            else:
                by_mid[irt] = iid
        sub = thread_key(str(item.get("subject") or ""))
        if sub:
            if sub in by_sub:
                union(iid, by_sub[sub])
            by_sub[sub] = iid

    buckets: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        buckets.setdefault(find(int(item["id"])), []).append(item)
    return list(buckets.values())


def short_summary(excerpt: str, reason: str = "") -> str:
    text = SPACES.sub(" ", excerpt or "").strip()
    chunks = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    chunks = [part for part in chunks if not part.startswith(">")][:2]
    out = " ".join(chunks).strip()
    if len(out) > 280:
        out = out[:279].rstrip() + "…"
    if out:
        return out
    return (reason or "").strip()[:280]
