from __future__ import annotations

from typing import Any

from regie.kernel import db


class Links:
    """Relations transverses entre modules. Jamais de lien orphelin : on nettoie à la suppression."""

    def __init__(self, dsn: str, org_id: str) -> None:
        self.dsn = dsn
        self.org_id = org_id

    def link(self, src_kind: str, src_id: Any, dst_kind: str, dst_id: Any, role: str = "", actor: str | None = None, meta: dict | None = None) -> int:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                INSERT INTO links (org_id, src_kind, src_id, dst_kind, dst_id, role, meta, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (src_kind, src_id, dst_kind, dst_id, role) DO UPDATE SET meta = EXCLUDED.meta
                RETURNING id
                """,
                (self.org_id, src_kind, str(src_id), dst_kind, str(dst_id), role, db.J(meta or {}), actor),
            )
            return int(row["id"]) if row else 0

    def unlink(self, src_kind: str, src_id: Any, dst_kind: str, dst_id: Any, role: str | None = None) -> int:
        with db.connect(self.dsn) as conn:
            if role is None:
                return db.execute(
                    conn,
                    "DELETE FROM links WHERE src_kind = %s AND src_id = %s AND dst_kind = %s AND dst_id = %s",
                    (src_kind, str(src_id), dst_kind, str(dst_id)),
                )
            return db.execute(
                conn,
                "DELETE FROM links WHERE src_kind = %s AND src_id = %s AND dst_kind = %s AND dst_id = %s AND role = %s",
                (src_kind, str(src_id), dst_kind, str(dst_id), role),
            )

    def of(self, kind: str, ident: Any, other_kind: str | None = None) -> list[dict[str, Any]]:
        """Tous les liens où l'entité est source ou destination, orientés depuis elle."""
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(
                conn,
                """
                SELECT id, src_kind, src_id, dst_kind, dst_id, role, meta, created_at,
                       CASE WHEN src_kind = %(k)s AND src_id = %(i)s THEN dst_kind ELSE src_kind END AS other_kind,
                       CASE WHEN src_kind = %(k)s AND src_id = %(i)s THEN dst_id ELSE src_id END AS other_id,
                       (src_kind = %(k)s AND src_id = %(i)s) AS outgoing
                FROM links
                WHERE (src_kind = %(k)s AND src_id = %(i)s) OR (dst_kind = %(k)s AND dst_id = %(i)s)
                ORDER BY created_at DESC
                """,
                {"k": kind, "i": str(ident)},
            )
        if other_kind:
            rows = [row for row in rows if row["other_kind"] == other_kind]
        return [db.jsonable(row) or {} for row in rows]

    def targets(self, kind: str, ident: Any, other_kind: str, role: str | None = None) -> list[str]:
        out = []
        for row in self.of(kind, ident, other_kind):
            if role is not None and row["role"] != role:
                continue
            out.append(str(row["other_id"]))
        return out

    def purge(self, kind: str, ident: Any) -> int:
        with db.connect(self.dsn) as conn:
            return db.execute(
                conn,
                "DELETE FROM links WHERE (src_kind = %s AND src_id = %s) OR (dst_kind = %s AND dst_id = %s)",
                (kind, str(ident), kind, str(ident)),
            )

    def relink(self, kind: str, old_id: Any, new_id: Any) -> int:
        """Fusion de doublons : les liens de l'ancien passent au nouveau."""
        with db.connect(self.dsn) as conn:
            a = db.execute(
                conn,
                "UPDATE links SET src_id = %s WHERE src_kind = %s AND src_id = %s AND NOT EXISTS (SELECT 1 FROM links l2 WHERE l2.src_kind = links.src_kind AND l2.src_id = %s AND l2.dst_kind = links.dst_kind AND l2.dst_id = links.dst_id AND l2.role = links.role)",
                (str(new_id), kind, str(old_id), str(new_id)),
            )
            b = db.execute(
                conn,
                "UPDATE links SET dst_id = %s WHERE dst_kind = %s AND dst_id = %s AND NOT EXISTS (SELECT 1 FROM links l2 WHERE l2.dst_kind = links.dst_kind AND l2.dst_id = %s AND l2.src_kind = links.src_kind AND l2.src_id = links.src_id AND l2.role = links.role)",
                (str(new_id), kind, str(old_id), str(new_id)),
            )
            db.execute(conn, "DELETE FROM links WHERE (src_kind = %s AND src_id = %s) OR (dst_kind = %s AND dst_id = %s)", (kind, str(old_id), kind, str(old_id)))
            return a + b
