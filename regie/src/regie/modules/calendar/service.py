"""Agendas : comptes Google (OAuth) ou ICS, synchro bidirectionnelle toutes les 60 s, conflits conservés, garde-fou d'écriture en masse."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from regie.connectors.google import FakeGoogle, GoogleCalendarConnector, SyncTokenExpired, from_google
from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.jobs import P_SCAN, P_USER, JobContext
from regie.kernel.registry import EntityKind

log = logging.getLogger("regie.calendar")
SYNC_SECONDS = 60
BULK_GUARD = 20
HISTORY_KEEP = 10
EVENT_FIELDS = ("title", "description", "location", "starts_at", "ends_at", "all_day", "status")


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CalendarService:
    name = "calendar"

    def __init__(self, kernel: Kernel, connector: GoogleCalendarConnector | FakeGoogle | None = None) -> None:
        self.kernel = kernel
        s = kernel.settings
        redirect = s.google_redirect_uri or (s.regie_public_url.rstrip("/") + "/api/v1/calendar/google/callback" if s.regie_public_url else "")
        self.google = connector or GoogleCalendarConnector(s.google_client_id, s.google_client_secret, redirect)
        self._states: dict[str, tuple[str, float]] = {}
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.connectors.register(self.google)
        k.registry.register(EntityKind(kind="calendar_event", module="calendar", label="Rendez-vous", icon="clock", table="calendar_events", fetch=self.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": (r.get("starts_at") or "")[:16].replace("T", " ")}, url=lambda i: f"/planning/week?event={i}"))
        k.actions.register(ActionSpec(kind="calendar.delete", module="calendar", entity_kind="calendar_event", label="Rendez-vous supprimé", apply=self._delete_apply, revert=self._delete_revert, snapshot=lambda ids: {i: (self.get(int(i)) or {}) for i in ids}, undoable=True))
        k.jobs.register("calendar.sync", self.job_sync, "google")
        k.jobs.register("calendar.push", self.job_push, "google")
        k.scheduler.ensure("calendar.sync", SYNC_SECONDS, priority=P_SCAN)

    # --- comptes ---------------------------------------------------------------------------------

    def accounts(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with db.connect(self.kernel.dsn) as conn:
            if user_id:
                rows = db.fetch_all(conn, "SELECT * FROM calendar_accounts WHERE org_id = %s AND (user_id = %s OR shared) ORDER BY shared, id", (self.kernel.org_id, user_id))
            else:
                rows = db.fetch_all(conn, "SELECT * FROM calendar_accounts WHERE org_id = %s ORDER BY id", (self.kernel.org_id,))
        out = []
        for row in rows:
            public = db.jsonable(row) or {}
            public["has_token"] = bool(self.kernel.secrets.get("google", f"refresh:{row['id']}")) if row["provider"] == "google" else None
            out.append(public)
        return out

    def account(self, account_id: int) -> dict[str, Any] | None:
        with db.connect(self.kernel.dsn) as conn:
            return db.jsonable(db.fetch_one(conn, "SELECT * FROM calendar_accounts WHERE id = %s AND org_id = %s", (account_id, self.kernel.org_id)))

    def begin_oauth(self, user_id: str, shared: bool = False, label: str = "") -> str:
        if not self.google.configured():
            raise RuntimeError("Google non configuré (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET).")
        state = secrets.token_urlsafe(24)
        self._states[state] = (f"{user_id}|{int(shared)}|{label}", datetime.now(timezone.utc).timestamp() + 900)
        return self.google.authorize_url(state)

    def finish_oauth(self, state: str, code: str) -> dict[str, Any]:
        packed = self._states.pop(state, None)
        if not packed or packed[1] < datetime.now(timezone.utc).timestamp():
            raise PermissionError("état OAuth inconnu ou expiré")
        user_id, shared, label = packed[0].split("|", 2)
        tokens = self.google.exchange_code(code)
        if not tokens.get("refresh_token"):
            raise RuntimeError("Google n'a pas renvoyé de jeton de rafraîchissement (retirer l'accès puis recommencer).")
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "INSERT INTO calendar_accounts (org_id, user_id, provider, email, calendar_id, label, shared) VALUES (%s, %s, 'google', %s, 'primary', %s, %s) RETURNING *", (self.kernel.org_id, user_id, tokens.get("email") or "", label or tokens.get("email") or "Google", shared == "1"))
        assert row is not None
        self.kernel.secrets.put("google", f"refresh:{row['id']}", tokens["refresh_token"])
        self.kernel.jobs.submit("calendar.sync", {"account_id": int(row["id"])}, P_USER, dedupe=True)
        return db.jsonable(row) or {}

    def add_ics(self, user_id: str, url: str, label: str, shared: bool = False) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "INSERT INTO calendar_accounts (org_id, user_id, provider, label, ics_url, shared, calendar_id) VALUES (%s, %s, 'ics', %s, %s, %s, '') RETURNING *", (self.kernel.org_id, user_id, label or "ICS", url, shared))
        self.kernel.jobs.submit("calendar.sync", {"account_id": int(row["id"])}, P_USER, dedupe=True)  # type: ignore[index]
        return db.jsonable(row) or {}

    def update_account(self, account_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in {"label", "color", "shared", "enabled", "calendar_id", "ics_url"} and v is not None}
        if not allowed:
            return self.account(account_id)
        sets = ", ".join(f"{k} = %s" for k in allowed)
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, f"UPDATE calendar_accounts SET {sets}, sync_token = CASE WHEN %s THEN '' ELSE sync_token END WHERE id = %s RETURNING *", [*allowed.values(), "calendar_id" in allowed, account_id])
        return db.jsonable(row)

    def remove_account(self, account_id: int) -> bool:
        self.kernel.secrets.delete("google", f"refresh:{account_id}")
        with db.connect(self.kernel.dsn) as conn:
            return db.execute(conn, "DELETE FROM calendar_accounts WHERE id = %s AND org_id = %s", (account_id, self.kernel.org_id)) > 0

    def calendars_of(self, account_id: int) -> list[dict[str, Any]]:
        token = self.kernel.secrets.get("google", f"refresh:{account_id}")
        if not token:
            return []
        return self.google.list_calendars(token)

    # --- événements ------------------------------------------------------------------------------

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        out = db.jsonable(row) or {}
        out.pop("history", None)
        return out

    def many(self, ids: list[str]) -> list[dict[str, Any]]:
        clean = [int(i) for i in ids if str(i).isdigit()]
        if not clean:
            return []
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM calendar_events WHERE id = ANY(%s)", (clean,))
        return [self._public(r) for r in rows]

    def get(self, event_id: int, with_history: bool = False) -> dict[str, Any] | None:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM calendar_events WHERE id = %s AND org_id = %s", (event_id, self.kernel.org_id))
        if not row:
            return None
        return (db.jsonable(row) or {}) if with_history else self._public(row)

    def range(self, start: str, end: str, user_id: str | None = None, account_ids: list[int] | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT e.*, a.label AS account_label, a.color AS account_color, a.shared AS account_shared, a.user_id AS account_user_id
            FROM calendar_events e JOIN calendar_accounts a ON a.id = e.account_id
            WHERE e.org_id = %s AND e.deleted_at IS NULL AND e.status <> 'cancelled' AND e.ends_at >= %s::timestamptz AND e.starts_at <= %s::timestamptz AND a.enabled
        """
        args: list[Any] = [self.kernel.org_id, start, end]
        if user_id:
            sql += " AND (a.user_id = %s OR a.shared)"
            args.append(user_id)
        if account_ids:
            sql += " AND a.id = ANY(%s)"
            args.append(account_ids)
        sql += " ORDER BY e.starts_at"
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, sql, args)
        return [self._public(r) for r in rows]

    def create(self, account_id: int, data: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        account = self.account(account_id)
        if not account:
            raise KeyError("agenda introuvable")
        if account["provider"] == "ics":
            raise PermissionError("un agenda ICS est en lecture seule")
        starts, ends = _dt(data.get("starts_at")), _dt(data.get("ends_at"))
        if not starts:
            raise ValueError("date de début requise")
        ends = ends or (starts + timedelta(hours=1))
        if ends < starts:
            raise ValueError("fin avant début")
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(
                conn,
                "INSERT INTO calendar_events (org_id, account_id, title, description, location, starts_at, ends_at, all_day, source, dirty, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'regie', true, %s) RETURNING *",
                (self.kernel.org_id, account_id, str(data.get("title") or "(sans titre)")[:300], str(data.get("description") or "")[:4000], str(data.get("location") or "")[:300], starts, ends, bool(data.get("all_day")), actor),
            )
        assert row is not None
        self.kernel.jobs.submit("calendar.push", {"account_id": account_id}, P_USER, dedupe=True)
        self.kernel.outbox.emit("calendar_event.created", {"id": int(row["id"]), "account_id": account_id})
        return self._public(row)

    def update(self, event_id: int, data: dict[str, Any], actor: str | None = None) -> dict[str, Any] | None:
        current = self.get(event_id, with_history=True)
        if not current:
            return None
        changes = {k: v for k, v in data.items() if k in EVENT_FIELDS}
        if "starts_at" in changes:
            changes["starts_at"] = _dt(changes["starts_at"])
        if "ends_at" in changes:
            changes["ends_at"] = _dt(changes["ends_at"])
        if not changes:
            return self._public(current)
        history = self._push_history(current, "regie", actor)
        sets = ", ".join(f"{k} = %s" for k in changes)
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, f"UPDATE calendar_events SET {sets}, dirty = true, local_updated_at = now(), history = %s WHERE id = %s RETURNING *", [*changes.values(), db.J(history), event_id])
        self.kernel.jobs.submit("calendar.push", {"account_id": int(current["account_id"])}, P_USER, dedupe=True)
        self.kernel.outbox.emit("calendar_event.updated", {"id": event_id})
        return self._public(row) if row else None

    def _push_history(self, current: dict[str, Any], origin: str, actor: str | None = None) -> list[dict[str, Any]]:
        history = list(current.get("history") or [])
        history.append({"at": db.iso(db.utcnow()), "origin": origin, "actor": actor, **{k: current.get(k) for k in EVENT_FIELDS}})
        return history[-HISTORY_KEEP:]

    def _delete_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        accounts: set[int] = set()
        for event_id in ctx.ids:
            row = self.get(int(event_id))
            if not row:
                continue
            with db.connect(self.kernel.dsn) as conn:
                db.execute(conn, "UPDATE calendar_events SET deleted_at = now(), dirty = true, local_updated_at = now() WHERE id = %s", (int(event_id),))
            accounts.add(int(row["account_id"]))
        for account_id in accounts:
            self.kernel.jobs.submit("calendar.push", {"account_id": account_id}, P_USER, dedupe=True)
        return {"deleted": ctx.ids}

    def _delete_revert(self, ctx: ActionCtx) -> None:
        for event_id in ctx.ids:
            with db.connect(self.kernel.dsn) as conn:
                db.execute(conn, "UPDATE calendar_events SET deleted_at = NULL, dirty = true, local_updated_at = now() WHERE id = %s", (int(event_id),))
            row = self.get(int(event_id))
            if row:
                self.kernel.jobs.submit("calendar.push", {"account_id": int(row["account_id"])}, P_USER, dedupe=True)

    # --- synchronisation ------------------------------------------------------------------------------

    def job_sync(self, ctx: JobContext) -> dict[str, Any]:
        only = ctx.payload.get("account_id")
        out: dict[str, Any] = {}
        for account in self.accounts():
            if not account.get("enabled") or (only and int(account["id"]) != int(only)):
                continue
            try:
                if account["provider"] == "ics":
                    out[str(account["id"])] = self._sync_ics(account)
                else:
                    out[str(account["id"])] = self._sync_google(account, ctx)
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_accounts SET status = 'ok', error = '', last_sync_at = now() WHERE id = %s", (account["id"],))
            except Exception as exc:
                log.warning("agenda %s : %s", account["id"], exc)
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_accounts SET status = 'error', error = %s WHERE id = %s", (str(exc)[:300], account["id"]))
                out[str(account["id"])] = {"error": str(exc)[:200]}
        if any("error" in v for v in out.values()):
            self.kernel.connectors.state.error("google", "; ".join(v.get("error", "") for v in out.values() if "error" in v)[:300])
        elif out:
            self.kernel.connectors.state.ok("google", meta={"accounts": len(out)})
        self.kernel.bus.publish("calendar", {"synced": list(out)})
        return out

    def _token(self, account: dict[str, Any]) -> str:
        token = self.kernel.secrets.get("google", f"refresh:{account['id']}")
        if not token:
            raise RuntimeError("jeton Google absent : reconnecter l'agenda (ou passer en ICS)")
        return token

    def _sync_google(self, account: dict[str, Any], ctx: JobContext | None) -> dict[str, Any]:
        token = self._token(account)
        calendar_id = account.get("calendar_id") or "primary"
        try:
            items, next_token = self.google.pull_events(token, calendar_id, account.get("sync_token") or "")
        except SyncTokenExpired:
            items, next_token = self.google.pull_events(token, calendar_id, "")
        pulled = conflicts = 0
        for item in items:
            mapped = from_google(item)
            if not mapped["external_id"]:
                continue
            conflicts += self._apply_remote(account, mapped)
            pulled += 1
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "UPDATE calendar_accounts SET sync_token = %s WHERE id = %s", (next_token, account["id"]))
        pushed = self._push_dirty(account, token)
        return {"pulled": pulled, "conflicts": conflicts, **pushed}

    def _apply_remote(self, account: dict[str, Any], mapped: dict[str, Any]) -> int:
        """Applique un changement distant. Conflit : la dernière modification gagne, l'autre version part dans l'historique."""
        with db.connect(self.kernel.dsn) as conn:
            local = db.fetch_one(conn, "SELECT * FROM calendar_events WHERE account_id = %s AND external_id = %s", (account["id"], mapped["external_id"]))
        remote_updated = _dt(mapped.get("remote_updated_at")) or db.utcnow()
        if mapped["status"] == "cancelled":
            if local and not local.get("deleted_at"):
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_events SET deleted_at = now(), dirty = false, etag = %s, remote_updated_at = %s WHERE id = %s", (mapped["etag"], remote_updated, local["id"]))
                self.kernel.outbox.emit("calendar_event.deleted", {"id": int(local["id"]), "origin": "google"})
            return 0
        if not mapped.get("starts_at"):
            return 0
        if local is None:
            with db.connect(self.kernel.dsn) as conn:
                row = db.fetch_one(
                    conn,
                    "INSERT INTO calendar_events (org_id, account_id, external_id, title, description, location, starts_at, ends_at, all_day, status, attendees, html_link, etag, remote_updated_at, source, dirty) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'google', false) RETURNING id",
                    (self.kernel.org_id, account["id"], mapped["external_id"], mapped["title"][:300], mapped["description"][:4000], mapped["location"][:300], mapped["starts_at"], mapped["ends_at"], mapped["all_day"], mapped["status"], db.J(mapped["attendees"]), mapped["html_link"], mapped["etag"], remote_updated),
                )
            self.kernel.outbox.emit("calendar_event.created", {"id": int(row["id"]), "origin": "google"})  # type: ignore[index]
            return 0
        if local.get("etag") == mapped["etag"]:
            return 0
        conflict = 0
        history = list(local.get("history") or [])
        if local.get("dirty"):
            local_updated = _dt(local.get("local_updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
            conflict = 1
            if local_updated > remote_updated:
                history.append({"at": db.iso(db.utcnow()), "origin": "google-lost", **{k: mapped.get(k) for k in EVENT_FIELDS}})
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_events SET history = %s, etag = %s, remote_updated_at = %s WHERE id = %s", (db.J(history[-HISTORY_KEEP:]), mapped["etag"], remote_updated, local["id"]))
                return conflict
            history.append({"at": db.iso(db.utcnow()), "origin": "regie-lost", **{k: (db.iso(local[k]) if isinstance(local.get(k), datetime) else local.get(k)) for k in EVENT_FIELDS}})
        else:
            history.append({"at": db.iso(db.utcnow()), "origin": "google", **{k: (db.iso(local[k]) if isinstance(local.get(k), datetime) else local.get(k)) for k in EVENT_FIELDS}})
        with db.connect(self.kernel.dsn) as conn:
            db.execute(
                conn,
                "UPDATE calendar_events SET title = %s, description = %s, location = %s, starts_at = %s, ends_at = %s, all_day = %s, status = %s, attendees = %s, html_link = %s, etag = %s, remote_updated_at = %s, dirty = false, deleted_at = NULL, history = %s WHERE id = %s",
                (mapped["title"][:300], mapped["description"][:4000], mapped["location"][:300], mapped["starts_at"], mapped["ends_at"], mapped["all_day"], mapped["status"], db.J(mapped["attendees"]), mapped["html_link"], mapped["etag"], remote_updated, db.J(history[-HISTORY_KEEP:]), local["id"]),
            )
        self.kernel.outbox.emit("calendar_event.updated", {"id": int(local["id"]), "origin": "google", "conflict": bool(conflict)})
        return conflict

    def job_push(self, ctx: JobContext) -> dict[str, Any]:
        account_id = int(ctx.payload.get("account_id") or 0)
        account = self.account(account_id)
        if not account or account["provider"] != "google":
            return {"skipped": True}
        return self._push_dirty(account, self._token(account), force=bool(ctx.payload.get("force")))

    def _push_dirty(self, account: dict[str, Any], token: str, force: bool = False) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM calendar_events WHERE account_id = %s AND dirty ORDER BY local_updated_at", (account["id"],))
        if not rows:
            if account.get("pending_bulk"):
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_accounts SET pending_bulk = 0 WHERE id = %s", (account["id"],))
            return {"pushed": 0}
        if len(rows) > BULK_GUARD and not force:
            with db.connect(self.kernel.dsn) as conn:
                db.execute(conn, "UPDATE calendar_accounts SET pending_bulk = %s, status = 'needs_confirmation' WHERE id = %s", (len(rows), account["id"]))
            if account.get("user_id"):
                self.kernel.notifications.notify([str(account["user_id"])], "calendar", f"{len(rows)} modifications en attente vers Google « {account.get('label')} » : confirme avant envoi.", "calendar_account", account["id"], "/planning/week")
            return {"pushed": 0, "held": len(rows)}
        pushed = deleted = failed = 0
        calendar_id = account.get("calendar_id") or "primary"
        for row in rows:
            try:
                if row.get("deleted_at"):
                    if row.get("external_id"):
                        self.google.delete_event(token, calendar_id, row["external_id"])
                    with db.connect(self.kernel.dsn) as conn:
                        db.execute(conn, "UPDATE calendar_events SET dirty = false, etag = '' WHERE id = %s", (row["id"],))
                    deleted += 1
                    continue
                try:
                    result = self.google.push_event(token, calendar_id, db.jsonable(row) or {}, row.get("external_id") or "", str(row.get("etag") or "") if row.get("external_id") else "")
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 412 or "412" in str(exc):
                        log.info("agenda %s : version distante plus récente, la synchro tranchera", row["id"])
                        continue  # reste « dirty » : le prochain pull applique la règle du conflit
                    raise
                with db.connect(self.kernel.dsn) as conn:
                    db.execute(conn, "UPDATE calendar_events SET external_id = %s, etag = %s, html_link = %s, remote_updated_at = %s, dirty = false WHERE id = %s", (str(result.get("id") or row.get("external_id") or ""), str(result.get("etag") or ""), str(result.get("htmlLink") or row.get("html_link") or ""), _dt(result.get("updated")) or db.utcnow(), row["id"]))
                self.kernel.connectors.refs.set("google", "calendar_event", row["id"], str(result.get("id") or ""), str(result.get("etag") or ""))
                pushed += 1
            except Exception as exc:
                failed += 1
                log.warning("push agenda %s : %s", row["id"], exc)
                self.kernel.connectors.refs.fail("google", "calendar_event", row["id"], str(exc))
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "UPDATE calendar_accounts SET pending_bulk = 0 WHERE id = %s", (account["id"],))
        if failed:
            raise RuntimeError(f"{failed} écriture(s) Google en échec")
        return {"pushed": pushed, "deleted": deleted}

    def confirm_bulk(self, account_id: int) -> dict[str, Any]:
        return self.kernel.jobs.submit("calendar.push", {"account_id": account_id, "force": True}, P_USER)

    def _sync_ics(self, account: dict[str, Any]) -> dict[str, Any]:
        """Repli lecture seule : ICS privé Google (ou tout autre)."""
        from icalendar import Calendar

        url = account.get("ics_url") or ""
        if not url:
            raise RuntimeError("URL ICS manquante")
        text = self._fetch_ics(url)
        cal = Calendar.from_ical(text)
        seen: set[str] = set()
        count = 0
        for component in cal.walk("VEVENT"):
            uid = str(component.get("uid") or "")
            if not uid:
                continue
            start = component.get("dtstart").dt if component.get("dtstart") else None
            end = component.get("dtend").dt if component.get("dtend") else start
            if start is None:
                continue
            all_day = not isinstance(start, datetime)
            if all_day:
                start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
                end = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) if end else start
            mapped = {"external_id": uid, "title": str(component.get("summary") or "(sans titre)"), "description": str(component.get("description") or ""), "location": str(component.get("location") or ""), "starts_at": _dt(start), "ends_at": _dt(end) or _dt(start), "all_day": all_day, "status": "cancelled" if str(component.get("status") or "").upper() == "CANCELLED" else "confirmed", "attendees": [], "html_link": "", "etag": str(component.get("last-modified").dt if component.get("last-modified") else component.get("sequence") or ""), "remote_updated_at": None}
            seen.add(uid)
            self._apply_remote(account, mapped)
            count += 1
        return {"pulled": count}

    def _fetch_ics(self, url: str) -> bytes:
        res = httpx.get(url, timeout=30, follow_redirects=True)
        res.raise_for_status()
        return res.content

    def history(self, event_id: int) -> list[dict[str, Any]]:
        row = self.get(event_id, with_history=True)
        return list(row.get("history") or []) if row else []
