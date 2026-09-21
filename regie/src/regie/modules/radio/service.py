"""Vie de la radio : invités, réservations, volontaires, partenariats, conducteur, valorisation, écoute."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.crud import Table
from regie.kernel.jobs import P_BACKGROUND, JobContext
from regie.kernel.registry import EntityKind
from regie.modules.radio import pdf

log = logging.getLogger("regie.radio")

GUEST_COLUMNS = {"person_id": "int", "name": "text", "topic": "text", "show_id": "int", "episode_id": "int", "planned_on": "date", "status": "text", "authorization_status": "text", "authorization_signed_at": "ts", "authorization_scope": "text", "attestation_path": "text", "notes": "text", "created_by": "uuid"}
GUEST_FLOW = {"idee": {"contacte", "annule"}, "contacte": {"confirme", "annule", "idee"}, "confirme": {"venu", "annule", "contacte"}, "venu": set(), "annule": {"idee"}}
RESOURCE_COLUMNS = {"name": "text", "kind": "text", "notes": "text", "active": "bool"}
BOOKING_COLUMNS = {"resource_id": "int", "title": "text", "user_id": "uuid", "starts_at": "ts", "ends_at": "ts", "notes": "text"}
VOLUNTEER_COLUMNS = {"person_id": "int", "kind": "text", "mission": "text", "start_date": "date", "end_date": "date", "hours_per_week": "float", "tutor_id": "uuid", "status": "text", "attestation_path": "text", "notes": "text"}
PARTNERSHIP_COLUMNS = {"organization_id": "int", "title": "text", "kind": "text", "contact_person_id": "int", "starts_on": "date", "ends_on": "date", "terms": "text", "value_eur": "float", "status": "text", "notes": "text"}
RUNDOWN_COLUMNS = {"show_id": "int", "episode_id": "int", "title": "text", "aired_on": "date", "items": "json", "status": "text", "created_by": "uuid"}
CHANNELS = ("site", "instagram", "facebook", "newsletter", "antenne", "partenaire")
ADMIN_DIR = "00-inbox/regie-documents"


class RadioService:
    name = "radio"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        args = (kernel.dsn, kernel.org_id)
        self.guests = Table(*args, "guests", GUEST_COLUMNS, order="planned_on DESC NULLS LAST, id DESC")
        self.resources = Table(*args, "resources", RESOURCE_COLUMNS, order="kind, name")
        self.bookings = Table(*args, "bookings", BOOKING_COLUMNS, order="starts_at")
        self.volunteers = Table(*args, "volunteers", VOLUNTEER_COLUMNS, order="status, start_date DESC NULLS LAST")
        self.partnerships = Table(*args, "partnerships", PARTNERSHIP_COLUMNS, order="status, starts_on DESC NULLS LAST")
        self.rundowns = Table(*args, "rundowns", RUNDOWN_COLUMNS, order="aired_on DESC NULLS LAST, id DESC")
        self.tables = {"guest": self.guests, "resource": self.resources, "booking": self.bookings, "volunteer": self.volunteers, "partnership": self.partnerships, "rundown": self.rundowns}
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.registry.register(EntityKind(kind="guest", module="radio", label="Invité", icon="mic", table="guests", fetch=self.guests.many, summarize=lambda r: {"title": r.get("name") or "", "subtitle": f"{r.get('status')} · {r.get('topic') or ''}", "status": r.get("status")}, url=lambda i: f"/radio/guests/{i}", actions=["radio.guest_status"]))
        k.registry.register(EntityKind(kind="booking", module="radio", label="Réservation", icon="key", table="bookings", fetch=self.bookings.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": (r.get("starts_at") or "")[:16].replace("T", " ")}, url=lambda i: f"/radio/bookings?id={i}"))
        k.registry.register(EntityKind(kind="volunteer", module="radio", label="Volontaire", icon="users", table="volunteers", fetch=self.volunteers.many, summarize=lambda r: {"title": r.get("mission") or r.get("kind") or "", "subtitle": r.get("status") or ""}, url=lambda i: f"/radio/volunteers/{i}"))
        k.registry.register(EntityKind(kind="partnership", module="radio", label="Partenariat", icon="handshake", table="partnerships", fetch=self.partnerships.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": r.get("status") or ""}, url=lambda i: f"/radio/partnerships/{i}"))
        k.registry.register(EntityKind(kind="rundown", module="radio", label="Conducteur", icon="list", table="rundowns", fetch=self.rundowns.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": str(r.get("aired_on") or "")}, url=lambda i: f"/radio/rundowns/{i}"))
        k.actions.register(ActionSpec(kind="radio.guest_status", module="radio", entity_kind="guest", label="Invité : changement d'étape", apply=self._guest_status_apply, revert=self._guest_status_revert, snapshot=lambda ids: {i: (self.guests.get(int(i)) or {}).get("status") for i in ids}, undoable=True))
        k.jobs.register("radio.icecast", self.job_icecast, "maintenance")
        k.scheduler.ensure("radio.icecast", 300, priority=P_BACKGROUND, enabled=bool(k.setting("radio.icecast_url")))
        k.outbox.on("podcast.published", lambda event: self.ensure_promotions("podcast", event.get("id")) if event.get("id") else None)
        k.outbox.on("coverage.planned", lambda event: self.ensure_promotions("event", event.get("event_id")) if event.get("event_id") else None)

    # --- invités et pipeline « idée invitée » ---------------------------------------------------------

    def create_guest(self, data: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        payload = {k: v for k, v in data.items() if k in GUEST_COLUMNS}
        payload["created_by"] = actor
        contacts = self.kernel.modules.get("contacts")
        if payload.get("person_id") and contacts:
            person = contacts.people.get(int(payload["person_id"]))
            if person and not payload.get("name"):
                payload["name"] = person["display_name"]
        if not payload.get("name"):
            raise ValueError("nom requis")
        row = self.guests.create(payload)
        if row.get("person_id"):
            self.kernel.links.link("guest", row["id"], "person", row["person_id"], role="person")
        if row.get("episode_id"):
            self.kernel.links.link("guest", row["id"], "episode", row["episode_id"], role="episode")
            self.kernel.links.link("episode", row["episode_id"], "person", row["person_id"], role="guest") if row.get("person_id") else None
        self.kernel.search.index("guest", row["id"], row["name"], row.get("topic") or "", row.get("notes") or "", url=f"/radio/guests/{row['id']}")
        self.kernel.outbox.emit("guest.created", {"id": row["id"], "status": row["status"]})
        return row

    def update_guest(self, guest_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        payload = {k: v for k, v in data.items() if k in GUEST_COLUMNS and k not in {"status", "created_by"}}
        if payload.get("authorization_status") == "signed" and not payload.get("authorization_signed_at"):
            payload["authorization_signed_at"] = db.utcnow()
        row = self.guests.update(guest_id, payload)
        if row:
            self.kernel.search.index("guest", row["id"], row["name"], row.get("topic") or "", row.get("notes") or "", url=f"/radio/guests/{row['id']}")
            if row.get("episode_id"):
                self.kernel.links.link("guest", row["id"], "episode", row["episode_id"], role="episode")
        return row

    def _guest_status_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        target = str(ctx.params.get("status") or "")
        for guest_id in ctx.ids:
            row = self.guests.get(int(guest_id))
            if not row:
                raise ValueError("invité introuvable")
            if target not in GUEST_FLOW.get(row["status"], set()):
                raise ValueError(f"passage {row['status']} → {target} impossible")
            if target == "venu" and row.get("authorization_status") != "signed":
                raise PermissionError("autorisation non signée : impossible de marquer l'invité comme venu")
            self.guests.update(int(guest_id), {"status": target})
            self.kernel.outbox.emit("guest.status", {"id": int(guest_id), "status": target})
        return {"status": target}

    def _guest_status_revert(self, ctx: ActionCtx) -> None:
        for guest_id, previous in ctx.before.items():
            self.guests.update(int(guest_id), {"status": previous or "idee"})

    def guest_pipeline(self) -> dict[str, list[dict[str, Any]]]:
        rows = self.guests.list("status <> 'venu' OR planned_on >= current_date - 30", [], 500)
        out: dict[str, list[dict[str, Any]]] = {status: [] for status in GUEST_FLOW}
        for row in rows:
            out.setdefault(row["status"], []).append(row)
        return out

    def guest_authorization_pdf(self, guest_id: int) -> tuple[str, bytes]:
        row = self.guests.get(guest_id)
        if not row:
            raise KeyError("invité introuvable")
        shows = self.kernel.modules.get("shows")
        show = shows.shows.get(row["show_id"]) if shows and row.get("show_id") else None
        details = [
            f"Je, soussigné(e) {row['name']}, autorise Radio Campus Orléans à enregistrer mon intervention" + (f" dans l'émission {show['name']}" if show else "") + (f" le {row['planned_on']}" if row.get("planned_on") else "") + ".",
            f"Cette autorisation couvre : {row.get('authorization_scope') or 'antenne, podcast, réseaux sociaux'}.",
            "Elle est accordée à titre gracieux, sans limitation de durée, pour un usage non commercial et associatif.",
            f"Sujet : {row.get('topic') or '—'}.",
        ]
        data = pdf.attestation("guest", row["name"], details)
        rel = f"{ADMIN_DIR}/autorisations/{row.get('planned_on') or 'sans-date'}_{_slug(row['name'])}_{row['id']}.pdf"
        if self.kernel.files.available and self.kernel.files.can_write(rel):
            self.kernel.files.write_bytes(rel, data, overwrite=True)
            self.guests.update(guest_id, {"attestation_path": rel})
            self.kernel.links.link("guest", guest_id, "file", rel, role="autorisation")
        return rel, data

    # --- réservations -----------------------------------------------------------------------------------

    def book(self, data: dict[str, Any], user_id: str | None) -> dict[str, Any]:
        resource = self.resources.get(int(data.get("resource_id") or 0))
        if not resource or not resource.get("active"):
            raise KeyError("ressource introuvable")
        starts, ends = _dt(data.get("starts_at")), _dt(data.get("ends_at"))
        if not starts or not ends or ends <= starts:
            raise ValueError("créneau invalide")
        with db.connect(self.kernel.dsn) as conn:
            clash = db.fetch_one(conn, "SELECT b.*, u.name AS who FROM bookings b LEFT JOIN users u ON u.id = b.user_id WHERE b.resource_id = %s AND b.starts_at < %s AND b.ends_at > %s LIMIT 1", (resource["id"], ends, starts))
        if clash:
            raise PermissionError(f"déjà réservé par {clash.get('who') or 'quelqu’un'} ({db.iso(clash['starts_at'])[:16]} → {db.iso(clash['ends_at'])[11:16]})")
        row = self.bookings.create({"resource_id": resource["id"], "title": str(data.get("title") or resource["name"])[:200], "user_id": user_id, "starts_at": starts, "ends_at": ends, "notes": str(data.get("notes") or "")[:1000]})
        self.kernel.outbox.emit("booking.created", {"id": row["id"], "resource": resource["name"], "user_id": user_id})
        return row

    def bookings_between(self, start: str, end: str) -> list[dict[str, Any]]:
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT b.*, r.name AS resource_name, r.kind AS resource_kind, u.name AS user_name FROM bookings b JOIN resources r ON r.id = b.resource_id LEFT JOIN users u ON u.id = b.user_id WHERE b.org_id = %s AND b.ends_at >= %s::timestamptz AND b.starts_at <= %s::timestamptz ORDER BY b.starts_at", (self.kernel.org_id, start, end))
        return [db.jsonable(r) or {} for r in rows]

    def cancel_booking(self, booking_id: int, user: dict[str, Any]) -> bool:
        row = self.bookings.get(booking_id)
        if not row:
            return False
        if user.get("role") != "admin" and str(row.get("user_id")) != str(user.get("id")):
            raise PermissionError("pas ta réservation")
        return self.bookings.delete(booking_id)

    # --- volontaires et partenariats ----------------------------------------------------------------------

    def create_volunteer(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.volunteers.create({k: v for k, v in data.items() if k in VOLUNTEER_COLUMNS})
        self.kernel.links.link("volunteer", row["id"], "person", row["person_id"], role="person")
        self.kernel.outbox.emit("volunteer.created", {"id": row["id"]})
        return row

    def volunteer_attestation(self, volunteer_id: int) -> tuple[str, bytes]:
        row = self.volunteers.get(volunteer_id)
        if not row:
            raise KeyError("volontaire introuvable")
        contacts = self.kernel.modules.get("contacts")
        person = contacts.people.get(int(row["person_id"])) if contacts else None
        name = person["display_name"] if person else f"volontaire {volunteer_id}"
        kind = "service_civique" if row.get("kind") == "service_civique" else "volunteer"
        details = [
            f"Radio Campus Orléans atteste que {name} a participé à la vie de la radio en qualité de {row.get('kind', 'bénévole').replace('_', ' ')}" + (f" du {row['start_date']}" if row.get("start_date") else "") + (f" au {row['end_date']}" if row.get("end_date") else "") + ".",
            f"Mission : {row.get('mission') or '—'}.",
            f"Volume horaire hebdomadaire : {row.get('hours_per_week') or '—'} h." if row.get("hours_per_week") else "",
        ]
        data = pdf.attestation(kind, name, [d for d in details if d])
        rel = f"{ADMIN_DIR}/attestations/{_slug(name)}_{volunteer_id}.pdf"
        if self.kernel.files.available and self.kernel.files.can_write(rel):
            self.kernel.files.write_bytes(rel, data, overwrite=True)
            self.volunteers.update(volunteer_id, {"attestation_path": rel})
            self.kernel.links.link("volunteer", volunteer_id, "file", rel, role="attestation")
        return rel, data

    def create_partnership(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.partnerships.create({k: v for k, v in data.items() if k in PARTNERSHIP_COLUMNS})
        self.kernel.links.link("partnership", row["id"], "organization", row["organization_id"], role="partner")
        if row.get("contact_person_id"):
            self.kernel.links.link("partnership", row["id"], "person", row["contact_person_id"], role="contact")
        self.kernel.search.index("partnership", row["id"], row["title"], row.get("kind") or "", row.get("terms") or "", url=f"/radio/partnerships/{row['id']}")
        self.kernel.outbox.emit("partnership.created", {"id": row["id"]})
        return row

    # --- conducteur ----------------------------------------------------------------------------------------------

    def save_rundown(self, data: dict[str, Any], rundown_id: int | None = None, actor: str | None = None) -> dict[str, Any]:
        payload = {k: v for k, v in data.items() if k in RUNDOWN_COLUMNS}
        items = []
        clock = 0.0
        for index, item in enumerate(payload.get("items") or []):
            duration = float(item.get("duration_s") or 0)
            items.append({"position": index, "at_s": round(clock, 1), "duration_s": duration, "kind": str(item.get("kind") or "parole"), "title": str(item.get("title") or "")[:200], "notes": str(item.get("notes") or "")[:2000], "person_id": item.get("person_id"), "done": bool(item.get("done"))})
            clock += duration
        payload["items"] = items
        if rundown_id is None:
            payload["created_by"] = actor
            row = self.rundowns.create(payload)
        else:
            row = self.rundowns.update(rundown_id, payload)
            if row is None:
                raise KeyError("conducteur introuvable")
        if row.get("episode_id"):
            self.kernel.links.link("rundown", row["id"], "episode", row["episode_id"], role="episode")
        row["total_s"] = round(clock, 1)
        return row

    # --- valorisation -------------------------------------------------------------------------------------------

    def ensure_promotions(self, entity_kind: str, entity_id: Any) -> list[dict[str, Any]]:
        summary = self.kernel.registry.summaries([(entity_kind, str(entity_id))]).get(f"{entity_kind}:{entity_id}") or {}
        title = summary.get("title") or entity_kind
        proposals = {
            "site": f"Nouveau sur orleans.radiocampus.org : {title}. À écouter et à partager.",
            "instagram": f"🎙️ {title} — c'est en ligne ! Lien en bio. #RadioCampusOrleans",
            "facebook": f"{title} est disponible. On vous raconte tout à l'antenne et en podcast → orleans.radiocampus.org",
            "newsletter": f"Cette semaine : {title}.",
            "antenne": f"Annonce antenne : « {title} », à retrouver sur le site et en podcast.",
            "partenaire": f"Bonjour, voici le lien vers « {title} » que nous avons diffusé. Merci pour votre accueil.",
        }
        with db.connect(self.kernel.dsn) as conn:
            for channel in CHANNELS:
                db.execute(conn, "INSERT INTO promotions (org_id, entity_kind, entity_id, channel, text_proposal) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", (self.kernel.org_id, entity_kind, str(entity_id), channel, proposals[channel]))
        return self.promotions(entity_kind, entity_id)

    def promotions(self, entity_kind: str, entity_id: Any) -> list[dict[str, Any]]:
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM promotions WHERE entity_kind = %s AND entity_id = %s ORDER BY id", (entity_kind, str(entity_id)))
        return [db.jsonable(r) or {} for r in rows]

    def tick_promotion(self, promotion_id: int, done: bool, user_id: str | None, text: str | None = None) -> dict[str, Any] | None:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "UPDATE promotions SET done = %s, done_at = CASE WHEN %s THEN now() ELSE NULL END, done_by = %s, text_proposal = COALESCE(%s, text_proposal) WHERE id = %s RETURNING *", (done, done, user_id if done else None, text, promotion_id))
        return db.jsonable(row)

    # --- écoute ------------------------------------------------------------------------------------------------

    def job_icecast(self, ctx: JobContext) -> dict[str, Any]:
        url = self.kernel.setting("radio.icecast_url")
        if not url:
            return {"skipped": True}
        res = httpx.get(url.rstrip("/") + "/status-json.xsl", timeout=15)
        res.raise_for_status()
        return {"rows": self.record_icecast(res.json())}

    def record_icecast(self, payload: dict[str, Any]) -> int:
        sources = (payload.get("icestats") or {}).get("source") or []
        if isinstance(sources, dict):
            sources = [sources]
        count = 0
        with db.connect(self.kernel.dsn) as conn:
            for source in sources:
                mount = str(source.get("listenurl") or source.get("mount") or "").rsplit("/", 1)[-1] or "stream"
                db.execute(conn, "INSERT INTO listening_stats (org_id, mount, listeners, peak, title) VALUES (%s, %s, %s, %s, %s)", (self.kernel.org_id, mount, int(source.get("listeners") or 0), int(source.get("listener_peak") or 0), str(source.get("title") or "")[:200]))
                count += 1
        return count

    def listening_summary(self, days: int = 7) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT mount, date_trunc('hour', at) AS hour, AVG(listeners) AS avg_listeners, MAX(listeners) AS max_listeners FROM listening_stats WHERE org_id = %s AND at > now() - make_interval(days => %s) GROUP BY mount, hour ORDER BY hour", (self.kernel.org_id, days))
            latest = db.fetch_all(conn, "SELECT DISTINCT ON (mount) mount, listeners, peak, title, at FROM listening_stats WHERE org_id = %s ORDER BY mount, at DESC", (self.kernel.org_id,))
        return {"hours": [db.jsonable(r) for r in rows], "latest": [db.jsonable(r) for r in latest]}


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _slug(text: str) -> str:
    import re
    import unicodedata

    base = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "x"


_ = timedelta
