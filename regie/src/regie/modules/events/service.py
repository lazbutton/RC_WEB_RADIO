"""Événements : cache Outlive rafraîchi toutes les 15 minutes, liens automatiques, couverture éditoriale."""

from __future__ import annotations

import logging
from typing import Any

from regie.connectors.outlive import OutliveConnector
from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.connectors import Connector
from regie.kernel.core import Kernel
from regie.kernel.crud import Table
from regie.kernel.jobs import P_SCAN, JobContext
from regie.kernel.registry import EntityKind

log = logging.getLogger("regie.events")
REFRESH_SECONDS = 15 * 60
COVERAGE_COLUMNS = {"event_id": "text", "kind": "text", "status": "text", "assignee_id": "uuid", "notes": "text", "created_by": "uuid"}
COVERAGE_KINDS = ("annonce", "interview", "direct", "chronique", "reportage", "partenariat")


class EventsService:
    name = "events"

    def __init__(self, kernel: Kernel, connector: Connector | None = None) -> None:
        self.kernel = kernel
        s = kernel.settings
        self.connector = connector or OutliveConnector(s.outlive_api_url, s.outlive_partner_key, s.outlive_supabase_url, s.outlive_anon_key, s.outlive_radio_campus_organizer_id)
        self.coverage = Table(kernel.dsn, kernel.org_id, "coverage", COVERAGE_COLUMNS, order="created_at DESC")
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.connectors.register(self.connector)
        k.registry.register(EntityKind(kind="event", module="events", label="Événement", icon="calendar", table="outlive_events", fetch=self.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": " · ".join(p for p in [(r.get("starts_at") or "")[:16].replace("T", " "), r.get("venue_name") or ""] if p), "starts_at": r.get("starts_at")}, url=lambda i: f"/events/{i}", actions=["events.cover"]))
        k.registry.register(EntityKind(kind="coverage", module="events", label="Couverture", icon="mic", table="coverage", fetch=self.coverage.many, summarize=lambda r: {"title": f"{r.get('kind')} · {r.get('status')}", "subtitle": r.get("notes") or ""}, url=lambda i: f"/events?coverage={i}"))
        k.actions.register(ActionSpec(kind="events.cover", module="events", entity_kind="event", label="Couvrir cet événement", apply=self._cover_apply, revert=self._cover_revert, undoable=True))
        k.jobs.register("events.refresh", self.job_refresh, "outlive")
        k.scheduler.ensure("events.refresh", REFRESH_SECONDS, priority=P_SCAN, enabled=self.connector.configured())

    # --- lecture ------------------------------------------------------------------------------

    def many(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM outlive_events WHERE id = ANY(%s)", ([str(i) for i in ids],))
        return [self._public(r) for r in rows]

    def get(self, event_id: str) -> dict[str, Any] | None:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM outlive_events WHERE id = %s", (event_id,))
        return self._public(row) if row else None

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        out = db.jsonable(row) or {}
        out.pop("raw", None)
        return out

    def list(self, *, start: str | None = None, end: str | None = None, q: str = "", only_radio: bool = False, organizer_id: str = "", venue_id: str = "", limit: int = 300) -> list[dict[str, Any]]:
        sql = "SELECT * FROM outlive_events WHERE org_id = %s AND gone_at IS NULL"
        args: list[Any] = [self.kernel.org_id]
        if start:
            sql += " AND COALESCE(ends_at, starts_at) >= %s::timestamptz"
            args.append(start)
        if end:
            sql += " AND starts_at <= %s::timestamptz"
            args.append(end)
        if q.strip():
            sql += " AND (regie_unaccent(title) ILIKE regie_unaccent(%s) OR regie_unaccent(venue_name) ILIKE regie_unaccent(%s) OR regie_unaccent(organizer_name) ILIKE regie_unaccent(%s))"
            needle = f"%{q.strip()}%"
            args.extend([needle, needle, needle])
        if only_radio:
            sql += " AND is_radio_campus"
        if organizer_id:
            sql += " AND organizer_id = %s"
            args.append(organizer_id)
        if venue_id:
            sql += " AND venue_id = %s"
            args.append(venue_id)
        sql += " ORDER BY starts_at LIMIT %s"
        args.append(limit)
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, sql, args)
        return [self._public(r) for r in rows]

    def coverage_for(self, event_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not event_ids:
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        for row in self.coverage.list("event_id = ANY(%s)", [event_ids], 2000):
            out.setdefault(str(row["event_id"]), []).append(row)
        return out

    # --- rafraîchissement ----------------------------------------------------------------------

    def refresh(self) -> dict[str, Any]:
        return self.kernel.jobs.run_inline("events.refresh", {})

    def job_refresh(self, ctx: JobContext) -> dict[str, Any]:
        k = self.kernel
        if not self.connector.configured():
            return {"skipped": True, "reason": "Outlive non configuré"}
        cursor = k.connectors.state.cursor("outlive")
        result = k.connectors.guarded("outlive", self.connector.pull, cursor)
        upserted = gone = warnings = linked = 0
        seen_ids: list[str] = []
        for change in result.changes:
            row = change.data
            seen_ids.append(change.external_id)
            if change.kind == "deleted":
                with db.connect(k.dsn) as conn:
                    gone += db.execute(conn, "UPDATE outlive_events SET gone_at = now() WHERE id = %s AND gone_at IS NULL", (change.external_id,))
                continue
            self._upsert(row)
            upserted += 1
            warnings += 1 if row.get("warnings") else 0
            linked += self._autolink(row)
        k.connectors.state.ok("outlive", cursor=result.cursor, meta={"upserted": upserted, "gone": gone})
        k.bus.publish("events", {"upserted": upserted, "gone": gone})
        if upserted:
            k.outbox.emit("events.refreshed", {"upserted": upserted, "gone": gone})
        return {"upserted": upserted, "gone": gone, "warnings": warnings, "linked": linked}

    def _upsert(self, row: dict[str, Any]) -> None:
        with db.connect(self.kernel.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO outlive_events (id, org_id, title, description, starts_at, ends_at, category, status, city, address, venue_id, venue_name, organizer_id, organizer_name,
                  artists, price, url, image_url, agenda_types, is_radio_campus, raw, warnings, source_updated_at, fetched_at, gone_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), NULL)
                ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, description = EXCLUDED.description, starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at,
                  category = EXCLUDED.category, status = EXCLUDED.status, city = EXCLUDED.city, address = EXCLUDED.address, venue_id = EXCLUDED.venue_id, venue_name = EXCLUDED.venue_name,
                  organizer_id = EXCLUDED.organizer_id, organizer_name = EXCLUDED.organizer_name, artists = EXCLUDED.artists, price = EXCLUDED.price, url = EXCLUDED.url,
                  image_url = EXCLUDED.image_url, agenda_types = EXCLUDED.agenda_types, is_radio_campus = EXCLUDED.is_radio_campus, raw = EXCLUDED.raw, warnings = EXCLUDED.warnings,
                  source_updated_at = EXCLUDED.source_updated_at, fetched_at = now(), gone_at = NULL
                """,
                (row["id"], self.kernel.org_id, row["title"], row.get("description") or "", row["starts_at"], row.get("ends_at"), row.get("category") or "", row.get("status") or "approved", row.get("city") or "", row.get("address") or "", row.get("venue_id") or "", row.get("venue_name") or "", row.get("organizer_id") or "", row.get("organizer_name") or "", db.J(row.get("artists") or []), row.get("price") or "", row.get("url") or "", row.get("image_url") or "", row.get("agenda_types") or [], bool(row.get("is_radio_campus")), db.J(row.get("raw") or {}), db.J(row.get("warnings") or []), row.get("source_updated_at")),
            )
        self.kernel.search.index("event", row["id"], row["title"], " · ".join(p for p in [row.get("venue_name") or "", row.get("organizer_name") or ""] if p), (row.get("description") or "")[:4000], url=f"/events/{row['id']}", meta={"starts_at": row["starts_at"]})

    def _autolink(self, row: dict[str, Any]) -> int:
        """Organisateur → structure, lieu → lieu, artiste → personne, quand la fiche existe déjà (jamais de création sauvage)."""
        contacts = self.kernel.modules.get("contacts")
        if contacts is None:
            return 0
        linked = 0
        org = contacts.organization_by_outlive(row.get("organizer_id") or "", row.get("organizer_name") or "")
        if org:
            if not org.get("outlive_organizer_id") and row.get("organizer_id"):
                contacts.organizations.update(org["id"], {"outlive_organizer_id": row["organizer_id"]})
            self.kernel.links.link("event", row["id"], "organization", org["id"], role="organizer")
            linked += 1
        place = contacts.place_by_outlive(row.get("venue_id") or "", row.get("venue_name") or "")
        if place:
            if not place.get("outlive_location_id") and row.get("venue_id"):
                contacts.places.update(place["id"], {"outlive_location_id": row["venue_id"]})
            self.kernel.links.link("event", row["id"], "place", place["id"], role="venue")
            linked += 1
        for artist in row.get("artists") or []:
            rows = contacts.people.list("merged_into IS NULL AND (outlive_artist_id = %s OR name_key = lower(regie_unaccent(%s)))", [artist.get("id") or "", artist.get("name") or ""], 1)
            if rows:
                self.kernel.links.link("event", row["id"], "person", rows[0]["id"], role="artist")
                linked += 1
        return linked

    # --- couverture ------------------------------------------------------------------------------

    def _cover_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        kind = str(ctx.params.get("kind") or "annonce")
        if kind not in COVERAGE_KINDS:
            raise ValueError("type de couverture inconnu")
        created = []
        for event_id in ctx.ids:
            if not self.get(event_id):
                raise ValueError(f"événement {event_id} inconnu")
            with db.connect(self.kernel.dsn) as conn:
                row = db.fetch_one(conn, "INSERT INTO coverage (org_id, event_id, kind, status, assignee_id, notes, created_by) VALUES (%s, %s, %s, 'planned', %s, %s, %s) ON CONFLICT (event_id, kind) DO UPDATE SET status = 'planned', assignee_id = COALESCE(EXCLUDED.assignee_id, coverage.assignee_id) RETURNING id", (self.kernel.org_id, event_id, kind, ctx.params.get("assignee_id") or ctx.actor_id, str(ctx.params.get("notes") or ""), ctx.actor_id))
            created.append(int(row["id"]))
            self.kernel.links.link("event", event_id, "coverage", row["id"], role=kind)
            if ctx.params.get("mail_id"):
                self.kernel.links.link("mail", ctx.params["mail_id"], "event", event_id, role="about")
            self.kernel.outbox.emit("coverage.planned", {"id": int(row["id"]), "event_id": event_id, "kind": kind, "assignee_id": ctx.params.get("assignee_id") or ctx.actor_id})
        return {"coverage_ids": created}

    def _cover_revert(self, ctx: ActionCtx) -> None:
        for cov_id in ctx.after.get("coverage_ids") or []:
            row = self.coverage.get(cov_id)
            if row:
                self.kernel.links.purge("coverage", cov_id)
                self.coverage.delete(cov_id)

    def update_coverage(self, cov_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        row = self.coverage.update(cov_id, {k: v for k, v in data.items() if k in {"kind", "status", "assignee_id", "notes"}})
        if row:
            self.kernel.outbox.emit("coverage.updated", {"id": cov_id, "status": row.get("status")})
        return row

    def agenda(self, start: str, end: str) -> list[dict[str, Any]]:
        """Événements couverts ou Radio Campus dans une fenêtre : sert la vue Semaine et les blocs publics."""
        rows = self.list(start=start, end=end, limit=500)
        covers = self.coverage_for([r["id"] for r in rows])
        for row in rows:
            row["coverage"] = covers.get(row["id"], [])
        return rows
