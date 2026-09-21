from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

SENDER_RE = re.compile(r'^\s*"?([^"<]+?)"?\s*<([^>]+)>')


def parse_sender(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "", ""
    match = SENDER_RE.match(text)
    if match:
        name = match.group(1).strip()
        email = match.group(2).strip()
        return name or email, email
    if "@" in text:
        return text.split("@", 1)[0], text
    return text, ""


def sender_name(raw: str) -> str:
    name, email = parse_sender(raw)
    return name or email or "—"


def sender_email(raw: str) -> str:
    _, email = parse_sender(raw)
    return email


def date_iso(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def relative_time(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return ""
    now = now or datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = dt.astimezone(PARIS)
    now_local = now.astimezone(PARIS)
    stamp = local.strftime("%d/%m/%Y %H:%M")
    delta_days = (now_local.date() - local.date()).days
    if delta_days <= 0:
        rel = local.strftime("%H:%M")
    elif delta_days == 1:
        rel = "hier"
    elif delta_days < 7:
        rel = f"il y a {delta_days} j"
    else:
        rel = local.strftime("%d/%m")
    return f"{stamp} ({rel})"


def confidence_pct(value: object) -> int:
    try:
        return int(round(max(0.0, min(1.0, float(value or 0))) * 100))
    except (TypeError, ValueError):
        return 0
