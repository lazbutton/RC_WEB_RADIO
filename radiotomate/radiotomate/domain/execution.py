"""Pure helpers for durable rundown status and programming identity."""

from __future__ import annotations

import hashlib

from radiotomate.enums import RundownStatus

STATUS_LABELS = {
    RundownStatus.PLANNED.value: "prévu",
    RundownStatus.RESERVED.value: "réservé",
    RundownStatus.PENDING_PUSH.value: "en attente de push",
    RundownStatus.SENT.value: "envoyé",
    RundownStatus.ACCEPTED.value: "accepté",
    RundownStatus.IN_QUEUE.value: "en file",
    RundownStatus.ON_AIR.value: "à l'antenne",
    RundownStatus.PLAYED.value: "joué",
    RundownStatus.SKIPPED.value: "sauté",
    RundownStatus.REPLACED.value: "remplacé",
    RundownStatus.RESCUE.value: "secours",
    RundownStatus.FAILED.value: "échec",
}

ENGAGED_STATUSES = {
    RundownStatus.RESERVED.value,
    RundownStatus.PENDING_PUSH.value,
    RundownStatus.SENT.value,
    RundownStatus.ACCEPTED.value,
    RundownStatus.IN_QUEUE.value,
    RundownStatus.ON_AIR.value,
}

REPLACEABLE_STATUSES = {
    RundownStatus.PLANNED.value,
    RundownStatus.RESCUE.value,
    RundownStatus.SKIPPED.value,
    RundownStatus.REPLACED.value,
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def item_status_code(item: dict) -> str:
    raw = str(item.get("status_code") or "").strip()
    if raw:
        return raw
    label = str(item.get("status") or "").strip()
    for code, text in STATUS_LABELS.items():
        if label == text:
            return code
    if item.get("fallback_used"):
        return RundownStatus.RESCUE.value
    return RundownStatus.PLANNED.value


def rundown_summary(items: list[dict]) -> dict:
    upcoming: list[dict] = []
    skipped = 0
    rescue = 0
    for item in items:
        code = item_status_code(item)
        if code == RundownStatus.SKIPPED.value:
            skipped += 1
            continue
        if code in {
            RundownStatus.PLAYED.value,
            RundownStatus.REPLACED.value,
        }:
            continue
        upcoming.append(item)
        if code == RundownStatus.RESCUE.value or item.get("fallback_used"):
            rescue += 1
    kinds = {kind: 0 for kind in ("musique", "jingle", "son", "pub")}
    for item in upcoming:
        kind = str(item.get("kind") or "")
        if kind in kinds:
            kinds[kind] += 1
    next_anchor = next(
        (item for item in upcoming if item.get("when") == "anchored"),
        None,
    )
    return {
        "next_anchor": (
            {
                "at": next_anchor.get("at"),
                "minute": next_anchor.get("minute"),
                "kind": next_anchor.get("kind"),
                "resource": next_anchor.get("resource"),
                "sync": next_anchor.get("sync"),
            }
            if next_anchor is not None
            else None
        ),
        "counts": {
            "items": len(upcoming),
            "musique": kinds["musique"],
            "jingle": kinds["jingle"],
            "son": kinds["son"],
            "pub": kinds["pub"],
            "secours": rescue,
            "sauté": skipped,
        },
    }


def programming_fingerprint(parts: list[str]) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def naive_datetime(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.replace(tzinfo=None)
    return value
