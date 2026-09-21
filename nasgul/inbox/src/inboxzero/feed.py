from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from inboxzero.classify import CATEGORY_LABELS

FEED_CATS = ("todo", "waiting")
FEED_LIMIT = 40
ATOM_NS = "http://www.w3.org/2005/Atom"


def select(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if (row.get("status") or "proposed") != "proposed":
            continue
        if row.get("category") not in FEED_CATS:
            continue
        out.append(row)
        if len(out) >= FEED_LIMIT:
            break
    return out


def _when(item: dict[str, Any]) -> str:
    value = (item.get("mailed_at") or item.get("created_at") or "").strip()
    if value:
        return value
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _attachment_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for row in item.get("attachments") or []:
        if not isinstance(row, dict):
            continue
        name = _text(row.get("filename"))
        if name:
            names.append(name[:120])
    return names[:12]


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def markdown(items: list[dict[str, Any]]) -> str:
    lines = [
        "# File À faire / En attente",
        "",
        f"{len(items)} mail{'s' if len(items) != 1 else ''} proposed. Rien n’est déplacé. Pas d’octets.",
        "",
    ]
    if not items:
        lines.append("File vide.")
        lines.append("")
        return "\n".join(lines)
    for item in items:
        subject = _text(item.get("subject")) or "(sans objet)"
        lines.append(f"## {subject}")
        lines.append("")
        who = _text(item.get("sender_name")) or _text(item.get("sender")) or "—"
        lines.append(f"- De : {who}")
        email = _text(item.get("sender_email"))
        if email:
            lines.append(f"- Mail : {email}")
        lines.append(f"- Catégorie : {_label(_text(item.get('category')))}")
        when = _text(item.get("created_rel")) or _when(item)
        lines.append(f"- Quand : {when}")
        notion_url = _text(item.get("notion_url"))
        if notion_url:
            lines.append(f"- Notion : {notion_url}")
        lines.append("")
        summary = _text(item.get("summary")) or _text(item.get("reason")) or "Pas encore de récap."
        lines.append(summary)
        lines.append("")
        names = _attachment_names(item)
        if names:
            lines.append("Pièces : " + ", ".join(names))
            lines.append("")
        draft = _text(item.get("draft"))
        if draft:
            lines.append("### Brouillon")
            lines.append("")
            lines.append(draft)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _el(parent: Element, tag: str, text: str = "", **attrs: str) -> Element:
    node = SubElement(parent, tag, attrs)
    if text:
        node.text = text
    return node


def atom_xml(items: list[dict[str, Any]], *, self_href: str) -> str:
    updated = _when(items[0]) if items else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    feed = Element("feed", {"xmlns": ATOM_NS})
    _el(feed, "title", "Inbox Zero — À faire / En attente")
    _el(feed, "id", "tag:inboxzero,file")
    _el(feed, "updated", updated)
    _el(feed, "subtitle", "File proposed. Rien n’est déplacé. Pas d’octets.")
    _el(feed, "link", href=self_href, rel="self")
    for item in items:
        entry = SubElement(feed, "entry")
        item_id = int(item.get("id") or 0)
        _el(entry, "id", f"tag:inboxzero,{item_id}")
        _el(entry, "title", _text(item.get("subject")) or "(sans objet)")
        _el(entry, "updated", _when(item))
        author = SubElement(entry, "author")
        _el(author, "name", _text(item.get("sender_name")) or _text(item.get("sender")) or "—")
        email = _text(item.get("sender_email"))
        if email:
            _el(author, "email", email)
        summary = _text(item.get("summary")) or _text(item.get("reason")) or "Pas encore de récap."
        _el(entry, "summary", summary)
        body = markdown([item]).strip()
        content = _el(entry, "content", body)
        content.set("type", "text")
        cat = _text(item.get("category"))
        if cat:
            _el(entry, "category", term=cat, label=_label(cat))
    return tostring(feed, encoding="unicode", xml_declaration=True)
