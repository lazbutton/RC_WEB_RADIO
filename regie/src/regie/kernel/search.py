from __future__ import annotations

import re
from typing import Any

from regie.kernel import db

TOKEN = re.compile(r"[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ''-]{0,40}")
STRIP = re.compile(r"[^0-9A-Za-zÀ-ÿ'-]")


def ts_query(raw: str) -> str:
    """Texte libre → requête tsquery à préfixes : « vivi form » → « vivi:* & form:* »."""
    parts: list[str] = []
    seen: set[str] = set()
    for token in TOKEN.findall(raw or ""):
        cleaned = STRIP.sub("", token).strip("'-").replace("'", "")
        if len(cleaned) < 2:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{cleaned}:*")
        if len(parts) >= 8:
            break
    return " & ".join(parts)


class Search:
    def __init__(self, dsn: str, org_id: str) -> None:
        self.dsn = dsn
        self.org_id = org_id

    def index(self, kind: str, ident: Any, title: str, subtitle: str = "", body: str = "", url: str = "", meta: dict | None = None) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO search_documents (kind, id, org_id, title, subtitle, body, url, meta, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (kind, id) DO UPDATE SET title = EXCLUDED.title, subtitle = EXCLUDED.subtitle, body = EXCLUDED.body,
                  url = EXCLUDED.url, meta = EXCLUDED.meta, updated_at = now()
                """,
                (kind, str(ident), self.org_id, (title or "")[:500], (subtitle or "")[:500], (body or "")[:20000], url or "", db.J(meta or {})),
            )

    def remove(self, kind: str, ident: Any) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM search_documents WHERE kind = %s AND id = %s", (kind, str(ident)))

    def count(self, kind: str | None = None) -> int:
        with db.connect(self.dsn) as conn:
            if kind:
                return int(db.scalar(conn, "SELECT COUNT(*) FROM search_documents WHERE kind = %s", (kind,)) or 0)
            return int(db.scalar(conn, "SELECT COUNT(*) FROM search_documents") or 0)

    def query(self, raw: str, kinds: list[str] | None = None, limit: int = 40) -> list[dict[str, Any]]:
        q = ts_query(raw)
        if not q:
            return []
        sql = """
            SELECT kind, id, title, subtitle, url, meta, updated_at,
                   ts_rank_cd(tsv, to_tsquery('simple', regie_unaccent(%(q)s))) AS rank,
                   ts_headline('simple', regie_unaccent(body), to_tsquery('simple', regie_unaccent(%(q)s)),
                               'MaxFragments=1, MaxWords=18, MinWords=6, StartSel="", StopSel=""') AS hit
            FROM search_documents
            WHERE tsv @@ to_tsquery('simple', regie_unaccent(%(q)s))
        """
        params: dict[str, Any] = {"q": q}
        if kinds:
            sql += " AND kind = ANY(%(kinds)s)"
            params["kinds"] = kinds
        sql += " ORDER BY rank DESC, updated_at DESC LIMIT %(limit)s"
        params["limit"] = limit
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, sql, params)
            if not rows:
                like = f"%{raw.strip()}%"
                rows = db.fetch_all(
                    conn,
                    """
                    SELECT kind, id, title, subtitle, url, meta, updated_at, similarity(regie_unaccent(title), regie_unaccent(%s)) AS rank, '' AS hit
                    FROM search_documents
                    WHERE regie_unaccent(title) ILIKE regie_unaccent(%s) AND (%s::text[] IS NULL OR kind = ANY(%s))
                    ORDER BY rank DESC, updated_at DESC LIMIT %s
                    """,
                    (raw.strip(), like, kinds, kinds, limit),
                )
        return [db.jsonable(row) or {} for row in rows]
