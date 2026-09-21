from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Summarize = Callable[[dict[str, Any]], dict[str, Any]]
Fetch = Callable[[list[str]], list[dict[str, Any]]]
IndexDoc = Callable[[dict[str, Any]], dict[str, str] | None]
Hook = Callable[[str], None]


@dataclass
class EntityKind:
    """Un type métier déclaré au noyau. Ajouter un type = un fichier côté API, un côté UI."""

    kind: str
    module: str
    label: str
    label_plural: str = ""
    icon: str = "dot"
    table: str = ""
    fetch: Fetch | None = None
    summarize: Summarize | None = None
    index_doc: IndexDoc | None = None
    on_delete: Hook | None = None
    url: Callable[[str], str] | None = None
    actions: list[str] = field(default_factory=list)

    def summary(self, row: dict[str, Any]) -> dict[str, Any]:
        base = {"kind": self.kind, "id": str(row.get("id")), "icon": self.icon, "label": self.label}
        if self.summarize:
            base.update(self.summarize(row))
        else:
            base.update({"title": str(row.get("title") or row.get("name") or row.get("subject") or row.get("id")), "subtitle": ""})
        if self.url:
            base["url"] = self.url(str(row.get("id")))
        return base


class EntityRegistry:
    def __init__(self) -> None:
        self._kinds: dict[str, EntityKind] = {}

    def register(self, kind: EntityKind) -> EntityKind:
        if kind.kind in self._kinds:
            raise ValueError(f"type déjà déclaré : {kind.kind}")
        if not kind.label_plural:
            kind.label_plural = kind.label + "s"
        self._kinds[kind.kind] = kind
        return kind

    def get(self, kind: str) -> EntityKind:
        try:
            return self._kinds[kind]
        except KeyError as exc:
            raise KeyError(f"type inconnu : {kind}") from exc

    def has(self, kind: str) -> bool:
        return kind in self._kinds

    def all(self) -> list[EntityKind]:
        return list(self._kinds.values())

    def public(self) -> list[dict[str, Any]]:
        return [
            {
                "kind": k.kind,
                "module": k.module,
                "label": k.label,
                "label_plural": k.label_plural,
                "icon": k.icon,
                "actions": list(k.actions),
            }
            for k in self._kinds.values()
        ]

    def summaries(self, refs: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        """Résumés (titre, sous-titre, icône) pour une liste de (kind, id), groupés par type."""
        out: dict[str, dict[str, Any]] = {}
        by_kind: dict[str, list[str]] = {}
        for kind, ident in refs:
            by_kind.setdefault(kind, []).append(str(ident))
        for kind, ids in by_kind.items():
            spec = self._kinds.get(kind)
            if not spec or not spec.fetch:
                for ident in ids:
                    out[f"{kind}:{ident}"] = {"kind": kind, "id": ident, "title": f"{kind} {ident}", "subtitle": "", "icon": "dot", "label": kind}
                continue
            rows = spec.fetch(ids)
            found = {str(row.get("id")): row for row in rows}
            for ident in ids:
                row = found.get(ident)
                out[f"{kind}:{ident}"] = spec.summary(row) if row else {"kind": kind, "id": ident, "title": "(supprimé)", "subtitle": "", "icon": spec.icon, "label": spec.label, "missing": True}
        return out
