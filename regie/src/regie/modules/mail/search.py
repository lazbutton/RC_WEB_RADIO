from __future__ import annotations

import re
from typing import Any

from regie.modules.mail.attachments import names, parse_list
from regie.modules.mail.sanitize import text as sanitize_text

TOKEN = re.compile(r"[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ''-]{0,40}")
SPECIAL = {"AND", "OR", "NOT", "NEAR"}
BODY_MAX = 12000


def fts_query(raw: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for token in TOKEN.findall(raw or ""):
        cleaned = re.sub(r'[*"^]', "", token).strip("'-")
        if len(cleaned) < 2:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        if cleaned.upper() in SPECIAL:
            parts.append(f'"{cleaned}"*')
        else:
            parts.append(f"{cleaned}*")
        if len(parts) >= 8:
            break
    return " AND ".join(parts)


def index_body(item: dict[str, Any]) -> str:
    chunks = [
        str(item.get("sender") or ""),
        str(item.get("subject") or ""),
        str(item.get("excerpt") or ""),
        str(item.get("summary") or ""),
        str(item.get("draft") or ""),
    ]
    atts = parse_list(item.get("attachments"))
    chunks.extend(names(atts))
    for row in atts:
        blob = str(row.get("text") or "").strip()
        if blob:
            chunks.append(blob[:800])
    packed = sanitize_text("\n".join(chunk for chunk in chunks if chunk).strip())
    return packed[:BODY_MAX]
