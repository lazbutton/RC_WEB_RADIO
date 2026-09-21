"""Petit CRUD générique pour les tables métier simples (une ligne = une entité, org_id, id bigserial)."""

from __future__ import annotations

from typing import Any

from regie.kernel import db


class Table:
    def __init__(self, dsn: str, org_id: str, name: str, columns: dict[str, str], *, json_columns: set[str] | None = None, order: str = "id DESC") -> None:
        """columns : nom → type logique ('text', 'int', 'float', 'bool', 'json', 'array', 'ts', 'date', 'uuid')."""
        self.dsn = dsn
        self.org_id = org_id
        self.name = name
        self.columns = columns
        self.json_columns = json_columns or {c for c, t in columns.items() if t == "json"}
        self.order = order

    def _coerce(self, column: str, value: Any) -> Any:
        kind = self.columns.get(column, "text")
        if value is None:
            return None
        if kind == "json":
            return db.J(value)
        if kind == "array":
            return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
        if kind == "bool":
            return bool(value)
        if kind in {"ts", "date"}:
            return value or None
        return str(value)

    def clean(self, data: dict[str, Any]) -> dict[str, Any]:
        return {key: self._coerce(key, value) for key, value in data.items() if key in self.columns}

    def create(self, data: dict[str, Any], **fixed: Any) -> dict[str, Any]:
        payload = self.clean(data)
        payload.update(fixed)
        payload["org_id"] = self.org_id
        cols = list(payload)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, f"INSERT INTO {self.name} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *", [payload[c] for c in cols])
        return db.jsonable(row) or {}

    def update(self, ident: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        payload = self.clean(data)
        if not payload:
            return self.get(ident)
        sets = ", ".join(f"{c} = %s" for c in payload)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, f"UPDATE {self.name} SET {sets} WHERE id = %s AND org_id = %s RETURNING *", [*payload.values(), ident, self.org_id])
        return db.jsonable(row)

    def get(self, ident: Any) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            return db.jsonable(db.fetch_one(conn, f"SELECT * FROM {self.name} WHERE id = %s AND org_id = %s", (ident, self.org_id)))

    def many(self, ids: list[Any]) -> list[dict[str, Any]]:
        clean = [int(i) for i in ids if str(i).lstrip("-").isdigit()]
        if not clean:
            return []
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, f"SELECT * FROM {self.name} WHERE id = ANY(%s) AND org_id = %s", (clean, self.org_id))
        return [db.jsonable(r) or {} for r in rows]

    def list(self, where: str = "", params: list[Any] | None = None, limit: int = 200, offset: int = 0, order: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {self.name} WHERE org_id = %s"
        args: list[Any] = [self.org_id]
        if where:
            sql += f" AND ({where})"
            args.extend(params or [])
        sql += f" ORDER BY {order or self.order} LIMIT %s OFFSET %s"
        args.extend([limit, offset])
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, sql, args)
        return [db.jsonable(r) or {} for r in rows]

    def count(self, where: str = "", params: list[Any] | None = None) -> int:
        sql = f"SELECT COUNT(*) FROM {self.name} WHERE org_id = %s"
        args: list[Any] = [self.org_id]
        if where:
            sql += f" AND ({where})"
            args.extend(params or [])
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, sql, args) or 0)

    def delete(self, ident: Any) -> bool:
        with db.connect(self.dsn) as conn:
            return db.execute(conn, f"DELETE FROM {self.name} WHERE id = %s AND org_id = %s", (ident, self.org_id)) > 0
