from __future__ import annotations

import json
import logging
from typing import Any

from regie.kernel import db
from regie.kernel.events import EventBus

log = logging.getLogger("regie.notifications")


class Notifications:
    def __init__(self, dsn: str, org_id: str, bus: EventBus, vapid: dict[str, str] | None = None) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.bus = bus
        self.vapid = vapid or {}

    def notify(self, user_ids: list[str], kind: str, text: str, entity_kind: str = "", entity_id: Any = "", url: str = "", push: bool = True) -> list[int]:
        ids: list[int] = []
        for user_id in dict.fromkeys(user_ids):
            if not user_id:
                continue
            with db.connect(self.dsn) as conn:
                row = db.fetch_one(
                    conn,
                    "INSERT INTO notifications (org_id, user_id, kind, text, entity_kind, entity_id, url) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (self.org_id, user_id, kind, text[:500], entity_kind, str(entity_id or ""), url),
                )
            if row:
                ids.append(int(row["id"]))
                self.bus.publish("notification", db.jsonable(row) or {}, user_id=str(user_id))
                if push:
                    self._push(str(user_id), text, url)
        return ids

    def list(self, user_id: str, unread_only: bool = False, limit: int = 40) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications WHERE user_id = %s"
        if unread_only:
            sql += " AND read_at IS NULL"
        sql += " ORDER BY id DESC LIMIT %s"
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, sql, (user_id, limit))
        return [db.jsonable(r) or {} for r in rows]

    def unread_count(self, user_id: str) -> int:
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND read_at IS NULL", (user_id,)) or 0)

    def mark_read(self, user_id: str, ids: list[int] | None = None) -> int:
        with db.connect(self.dsn) as conn:
            if ids:
                return db.execute(conn, "UPDATE notifications SET read_at = now() WHERE user_id = %s AND id = ANY(%s) AND read_at IS NULL", (user_id, ids))
            return db.execute(conn, "UPDATE notifications SET read_at = now() WHERE user_id = %s AND read_at IS NULL", (user_id,))

    def subscribe_push(self, user_id: str, subscription: dict[str, Any], user_agent: str = "") -> None:
        endpoint = str(subscription.get("endpoint") or "")
        if not endpoint:
            raise ValueError("abonnement push invalide")
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                "INSERT INTO push_subscriptions (user_id, endpoint, keys, user_agent) VALUES (%s, %s, %s, %s) ON CONFLICT (endpoint) DO UPDATE SET keys = EXCLUDED.keys, user_id = EXCLUDED.user_id",
                (user_id, endpoint, db.J(subscription.get("keys") or {}), user_agent[:200]),
            )

    def unsubscribe_push(self, endpoint: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))

    def _push(self, user_id: str, text: str, url: str) -> None:
        if not self.vapid.get("private_key") or not self.vapid.get("public_key"):
            return
        try:
            from pywebpush import WebPushException, webpush
        except Exception:
            return
        with db.connect(self.dsn) as conn:
            subs = db.fetch_all(conn, "SELECT endpoint, keys FROM push_subscriptions WHERE user_id = %s", (user_id,))
        for sub in subs:
            try:
                webpush(
                    subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=json.dumps({"title": "Régie", "body": text[:200], "url": url}),
                    vapid_private_key=self.vapid["private_key"],
                    vapid_claims={"sub": self.vapid.get("subject", "mailto:regie@example.org")},
                    timeout=8,
                )
            except WebPushException as exc:  # abonnement mort : on le retire
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    self.unsubscribe_push(sub["endpoint"])
                else:
                    log.warning("web push : %s", exc)
            except Exception as exc:
                log.warning("web push : %s", exc)
