"""Contacts : personnes, structures, lieux. Dédoublonnage, fusion réversible, import CardDAV/expéditeurs, fiches 360."""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any

from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.crud import Table
from regie.kernel.registry import EntityKind

log = logging.getLogger("regie.contacts")

PEOPLE_COLUMNS = {"display_name": "text", "first_name": "text", "last_name": "text", "emails": "json", "phones": "json", "job_title": "text", "notes": "text", "tags": "array", "source": "text", "carddav_uid": "text", "outlive_artist_id": "text", "merged_into": "int", "retention_until": "date"}
ORG_COLUMNS = {"name": "text", "kind": "text", "website": "text", "emails": "json", "phones": "json", "domains": "array", "address": "text", "notes": "text", "tags": "array", "outlive_organizer_id": "text", "merged_into": "int"}
PLACE_COLUMNS = {"name": "text", "address": "text", "city": "text", "lat": "float", "lng": "float", "capacity": "int", "website": "text", "notes": "text", "outlive_location_id": "text", "merged_into": "int"}
GENERIC_DOMAINS = {"gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.fr", "yahoo.com", "orange.fr", "free.fr", "wanadoo.fr", "laposte.net", "sfr.fr", "icloud.com", "live.fr", "protonmail.com", "proton.me"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)
KINDS = {"person": "people", "organization": "organizations", "place": "places"}


def norm_email(value: str) -> str:
    return (value or "").strip().lower()


def domain_of(email: str) -> str:
    email = norm_email(email)
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def split_sender(sender: str) -> tuple[str, str]:
    """« Viviane Berreur <v@x.org> » → (nom, email)."""
    match = EMAIL_RE.search(sender or "")
    email = norm_email(match.group(0)) if match else ""
    name = re.sub(r"<[^>]*>", "", sender or "").replace('"', "").strip().strip(",")
    if not name and email:
        local = email.split("@", 1)[0]
        name = " ".join(part.capitalize() for part in re.split(r"[._-]+", local) if part)
    return name, email


class ContactsService:
    name = "contacts"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.people = Table(kernel.dsn, kernel.org_id, "people", PEOPLE_COLUMNS, order="display_name")
        self.organizations = Table(kernel.dsn, kernel.org_id, "organizations", ORG_COLUMNS, order="name")
        self.places = Table(kernel.dsn, kernel.org_id, "places", PLACE_COLUMNS, order="name")
        self.tables = {"person": self.people, "organization": self.organizations, "place": self.places}
        self._register()

    # --- noyau ------------------------------------------------------------------------

    def _register(self) -> None:
        k = self.kernel
        k.registry.register(EntityKind(kind="person", module="contacts", label="Personne", icon="user", table="people", fetch=self.people.many, summarize=lambda r: {"title": r.get("display_name") or "", "subtitle": (r.get("emails") or [""])[0] if r.get("emails") else (r.get("job_title") or "")}, url=lambda i: f"/contacts/person/{i}", actions=["contacts.merge"]))
        k.registry.register(EntityKind(kind="organization", module="contacts", label="Structure", icon="building", table="organizations", fetch=self.organizations.many, summarize=lambda r: {"title": r.get("name") or "", "subtitle": r.get("kind") or ""}, url=lambda i: f"/contacts/organization/{i}", actions=["contacts.merge"]))
        k.registry.register(EntityKind(kind="place", module="contacts", label="Lieu", icon="pin", table="places", fetch=self.places.many, summarize=lambda r: {"title": r.get("name") or "", "subtitle": r.get("city") or r.get("address") or ""}, url=lambda i: f"/contacts/place/{i}", actions=["contacts.merge"]))
        k.actions.register(ActionSpec(kind="contacts.merge", module="contacts", entity_kind="person", label="Fusion de doublons", apply=self._merge_apply, revert=self._merge_revert, snapshot=self._merge_snapshot, undoable=True))
        k.actions.register(ActionSpec(kind="contacts.create_from_mail", module="contacts", entity_kind="mail", label="Fiche créée depuis un mail", apply=self._create_from_mail, revert=self._delete_created, undoable=True))
        k.jobs.register("contacts.link_mail", self._job_link_mail, "maintenance")
        k.jobs.register("contacts.import_senders", self._job_import_senders, "maintenance")
        k.outbox.on("mail.received", lambda event: k.jobs.submit("contacts.link_mail", {"item_id": event.get("id")}, 3, dedupe=True) if event.get("id") else None)
        k.outbox.on("person.*", lambda event: self.reindex("person", event.get("id")) if event.get("id") else None)

    def reindex(self, kind: str, ident: Any) -> None:
        table = self.tables[kind]
        row = table.get(ident)
        if not row or row.get("merged_into"):
            self.kernel.search.remove(kind, ident)
            return
        if kind == "person":
            self.kernel.search.index(kind, ident, row["display_name"], ", ".join(row.get("emails") or []), " ".join([row.get("job_title") or "", row.get("notes") or "", " ".join(row.get("tags") or [])]), url=f"/contacts/person/{ident}")
        elif kind == "organization":
            self.kernel.search.index(kind, ident, row["name"], row.get("kind") or "", " ".join([row.get("notes") or "", row.get("website") or "", " ".join(row.get("emails") or []), " ".join(row.get("tags") or [])]), url=f"/contacts/organization/{ident}")
        else:
            self.kernel.search.index(kind, ident, row["name"], row.get("city") or "", " ".join([row.get("address") or "", row.get("notes") or ""]), url=f"/contacts/place/{ident}")

    # --- CRUD -----------------------------------------------------------------------------

    def create(self, kind: str, data: dict[str, Any], actor: str | None = None) -> dict[str, Any]:
        table = self.tables[kind]
        data = dict(data)
        if kind == "person":
            data["emails"] = sorted({norm_email(e) for e in (data.get("emails") or []) if norm_email(e)})
            if not data.get("display_name"):
                data["display_name"] = " ".join(p for p in [data.get("first_name", ""), data.get("last_name", "")] if p).strip() or (data["emails"][0] if data["emails"] else "Sans nom")
        if kind == "organization":
            data["emails"] = sorted({norm_email(e) for e in (data.get("emails") or []) if norm_email(e)})
            domains = set(data.get("domains") or [])
            domains.update(d for d in (domain_of(e) for e in data["emails"]) if d and d not in GENERIC_DOMAINS)
            data["domains"] = sorted(domains)
        row = table.create(data)
        self.reindex(kind, row["id"])
        self.kernel.outbox.emit(f"{kind}.created", {"id": row["id"], "actor": actor})
        return row

    def update(self, kind: str, ident: Any, data: dict[str, Any]) -> dict[str, Any] | None:
        data = dict(data)
        if "emails" in data:
            data["emails"] = sorted({norm_email(e) for e in (data.get("emails") or []) if norm_email(e)})
        row = self.tables[kind].update(ident, data)
        if row:
            self.reindex(kind, ident)
            self.kernel.outbox.emit(f"{kind}.updated", {"id": row["id"]})
        return row

    def delete(self, kind: str, ident: Any) -> bool:
        ok = self.tables[kind].delete(ident)
        if ok:
            self.kernel.links.purge(kind, ident)
            self.kernel.search.remove(kind, ident)
            self.kernel.outbox.emit(f"{kind}.deleted", {"id": ident})
        return ok

    def list(self, kind: str, q: str = "", limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        table = self.tables[kind]
        if q.strip():
            needle = f"%{q.strip()}%"
            col = "display_name" if kind == "person" else "name"
            if kind == "person":
                return table.list(f"merged_into IS NULL AND (regie_unaccent({col}) ILIKE regie_unaccent(%s) OR emails::text ILIKE %s)", [needle, needle], limit, offset)
            return table.list(f"merged_into IS NULL AND regie_unaccent({col}) ILIKE regie_unaccent(%s)", [needle], limit, offset)
        return table.list("merged_into IS NULL", [], limit, offset)

    # --- correspondances ----------------------------------------------------------------------

    def person_by_email(self, email: str) -> dict[str, Any] | None:
        email = norm_email(email)
        if not email:
            return None
        rows = self.people.list("merged_into IS NULL AND emails @> %s", [db.J([email])], 1)
        return rows[0] if rows else None

    def organization_by_domain(self, domain: str) -> dict[str, Any] | None:
        if not domain or domain in GENERIC_DOMAINS:
            return None
        rows = self.organizations.list("merged_into IS NULL AND %s = ANY(domains)", [domain], 1)
        return rows[0] if rows else None

    def organization_by_outlive(self, outlive_id: str, name: str = "") -> dict[str, Any] | None:
        if outlive_id:
            rows = self.organizations.list("merged_into IS NULL AND outlive_organizer_id = %s", [outlive_id], 1)
            if rows:
                return rows[0]
        if name:
            rows = self.organizations.list("merged_into IS NULL AND name_key = lower(regie_unaccent(%s))", [name], 1)
            return rows[0] if rows else None
        return None

    def place_by_outlive(self, outlive_id: str, name: str = "") -> dict[str, Any] | None:
        if outlive_id:
            rows = self.places.list("merged_into IS NULL AND outlive_location_id = %s", [outlive_id], 1)
            if rows:
                return rows[0]
        if name:
            rows = self.places.list("merged_into IS NULL AND name_key = lower(regie_unaccent(%s))", [name], 1)
            return rows[0] if rows else None
        return None

    def duplicates(self, kind: str = "person") -> list[dict[str, Any]]:
        """Candidats au dédoublonnage : même e-mail ou même nom normalisé."""
        out: list[dict[str, Any]] = []
        with db.connect(self.kernel.dsn) as conn:
            if kind == "person":
                rows = db.fetch_all(conn, "SELECT e.value AS key, array_agg(p.id ORDER BY p.id) AS ids FROM people p, jsonb_array_elements_text(p.emails) e WHERE p.org_id = %s AND p.merged_into IS NULL GROUP BY e.value HAVING COUNT(*) > 1", (self.kernel.org_id,))
                out.extend({"reason": "même e-mail", "key": r["key"], "ids": [int(i) for i in r["ids"]]} for r in rows)
            table = "people" if kind == "person" else ("organizations" if kind == "organization" else "places")
            rows = db.fetch_all(conn, f"SELECT name_key AS key, array_agg(id ORDER BY id) AS ids FROM {table} WHERE org_id = %s AND merged_into IS NULL AND name_key <> '' GROUP BY name_key HAVING COUNT(*) > 1", (self.kernel.org_id,))
            out.extend({"reason": "même nom", "key": r["key"], "ids": [int(i) for i in r["ids"]]} for r in rows)
        return out

    # --- fusion réversible ---------------------------------------------------------------------------

    def _merge_snapshot(self, ids: list[str]) -> dict[str, Any]:
        return {}

    def _merge_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        kind = str(ctx.params.get("kind") or "person")
        table = self.tables[kind]
        keep_id = int(ctx.params.get("keep") or ctx.ids[0])
        losers = [int(i) for i in ctx.ids if int(i) != keep_id]
        keep = table.get(keep_id)
        if not keep:
            raise ValueError("fiche à conserver introuvable")
        before = {"keep": keep, "keep_links": self.kernel.links.of(kind, keep_id), "losers": {}}
        for loser_id in losers:
            loser = table.get(loser_id)
            if not loser:
                continue
            before["losers"][str(loser_id)] = {"row": loser, "links": self.kernel.links.of(kind, loser_id)}
            merged: dict[str, Any] = {}
            if kind == "person":
                merged["emails"] = sorted(set(keep.get("emails") or []) | set(loser.get("emails") or []))
                merged["phones"] = list({*(keep.get("phones") or []), *(loser.get("phones") or [])})
                for field in ("job_title", "first_name", "last_name", "carddav_uid", "outlive_artist_id"):
                    if not keep.get(field) and loser.get(field):
                        merged[field] = loser[field]
            elif kind == "organization":
                merged["emails"] = sorted(set(keep.get("emails") or []) | set(loser.get("emails") or []))
                merged["domains"] = sorted(set(keep.get("domains") or []) | set(loser.get("domains") or []))
                for field in ("website", "address", "outlive_organizer_id"):
                    if not keep.get(field) and loser.get(field):
                        merged[field] = loser[field]
            else:
                for field in ("address", "city", "lat", "lng", "outlive_location_id"):
                    if not keep.get(field) and loser.get(field):
                        merged[field] = loser[field]
            notes = "\n".join(n for n in [keep.get("notes") or "", loser.get("notes") or ""] if n)
            merged["notes"] = notes
            merged["tags"] = sorted(set(keep.get("tags") or []) | set(loser.get("tags") or []))
            keep = table.update(keep_id, merged) or keep
            self.kernel.links.relink(kind, loser_id, keep_id)
            table.update(loser_id, {"merged_into": keep_id})
            with db.connect(self.kernel.dsn) as conn:
                if kind == "person":
                    db.execute(conn, "UPDATE affiliations SET person_id = %s WHERE person_id = %s AND NOT EXISTS (SELECT 1 FROM affiliations a2 WHERE a2.person_id = %s AND a2.organization_id = affiliations.organization_id AND a2.role = affiliations.role)", (keep_id, loser_id, keep_id))
                    db.execute(conn, "UPDATE interactions SET person_id = %s WHERE person_id = %s", (keep_id, loser_id))
                elif kind == "organization":
                    db.execute(conn, "UPDATE affiliations SET organization_id = %s WHERE organization_id = %s AND NOT EXISTS (SELECT 1 FROM affiliations a2 WHERE a2.organization_id = %s AND a2.person_id = affiliations.person_id AND a2.role = affiliations.role)", (keep_id, loser_id, keep_id))
                    db.execute(conn, "UPDATE interactions SET organization_id = %s WHERE organization_id = %s", (keep_id, loser_id))
            self.kernel.search.remove(kind, loser_id)
        ctx.before.update(before)
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "UPDATE actions SET before = %s WHERE id = %s", (db.J(before), ctx.action_id))
        self.reindex(kind, keep_id)
        self.kernel.outbox.emit(f"{kind}.merged", {"keep": keep_id, "losers": losers})
        return {"keep": keep_id, "merged": losers}

    def _merge_revert(self, ctx: ActionCtx) -> None:
        """Remet les fiches et leurs liens exactement comme avant la fusion."""
        kind = str(ctx.params.get("kind") or "person")
        table = self.tables[kind]
        keep = ctx.before.get("keep") or {}

        def restore_links(ident: int, links: list[dict[str, Any]]) -> None:
            self.kernel.links.purge(kind, ident)
            for link in links:
                self.kernel.links.link(link["src_kind"], link["src_id"], link["dst_kind"], link["dst_id"], link.get("role") or "")

        if keep:
            table.update(int(keep["id"]), {k: v for k, v in keep.items() if k in table.columns and k != "merged_into"})
            restore_links(int(keep["id"]), ctx.before.get("keep_links") or [])
            self.reindex(kind, int(keep["id"]))
        for loser_id, snap in (ctx.before.get("losers") or {}).items():
            row = snap.get("row") or {}
            table.update(int(loser_id), {**{k: v for k, v in row.items() if k in table.columns}, "merged_into": None})
            restore_links(int(loser_id), snap.get("links") or [])
            self.reindex(kind, int(loser_id))

    def merge(self, kind: str, keep_id: int, other_ids: list[int], actor: str | None = None) -> dict[str, Any]:
        ids = [str(keep_id), *[str(i) for i in other_ids if int(i) != keep_id]]
        if len(ids) < 2:
            raise ValueError("il faut au moins deux fiches")
        return self.kernel.actions.perform("contacts.merge", ids, {"kind": kind, "keep": keep_id}, actor_id=actor, label=f"Fusion de {len(ids)} fiches")

    # --- depuis un mail --------------------------------------------------------------------------------

    def _create_from_mail(self, ctx: ActionCtx) -> dict[str, Any]:
        mail = self.kernel.modules.get("mail")
        if mail is None:
            raise RuntimeError("module Mails absent")
        created: list[int] = []
        linked: list[int] = []
        for item_id in ctx.ids:
            item = mail.store.get_item(int(item_id))
            if not item:
                continue
            name, email = split_sender(item.get("sender") or "")
            person = self.person_by_email(email) if email else None
            if person is None:
                person = self.create("person", {"display_name": name or email, "emails": [email] if email else [], "source": "mail"}, actor=ctx.actor_id)
                created.append(int(person["id"]))
            linked.append(int(person["id"]))
            self.kernel.links.link("mail", item_id, "person", person["id"], role="sender", actor=ctx.actor_id)
            org = self.organization_by_domain(domain_of(email))
            if org:
                self.kernel.links.link("mail", item_id, "organization", org["id"], role="sender_org")
            self.record_interaction("mail", person_id=int(person["id"]), summary=item.get("subject") or "", ref_kind="mail", ref_id=item_id, at=item.get("mailed_at") or None, actor=ctx.actor_id)
        return {"created": created, "people": linked}

    def _delete_created(self, ctx: ActionCtx) -> None:
        for person_id in ctx.after.get("created") or []:
            self.delete("person", int(person_id))

    def _job_link_mail(self, job) -> dict[str, Any]:
        """Liaison automatique d'un mail reçu à une personne / structure connue."""
        mail = self.kernel.modules.get("mail")
        item_id = int(job.payload.get("item_id") or 0)
        if mail is None or not item_id:
            return {"skipped": True}
        item = mail.store.get_item(item_id)
        if not item:
            return {"skipped": True}
        _name, email = split_sender(item.get("sender") or "")
        out = {"person": None, "organization": None}
        person = self.person_by_email(email)
        if person:
            self.kernel.links.link("mail", item_id, "person", person["id"], role="sender")
            self.record_interaction("mail", person_id=int(person["id"]), summary=item.get("subject") or "", ref_kind="mail", ref_id=item_id, at=item.get("mailed_at") or None)
            out["person"] = person["id"]
        org = self.organization_by_domain(domain_of(email))
        if org:
            self.kernel.links.link("mail", item_id, "organization", org["id"], role="sender_org")
            out["organization"] = org["id"]
        return out

    def _job_import_senders(self, job) -> dict[str, Any]:
        """Expéditeurs fréquents → fiches personnes (source = mail), sans doublon."""
        mail = self.kernel.modules.get("mail")
        if mail is None:
            return {"created": 0}
        created = 0
        for row in mail.store.frequent_senders(min_count=int(job.payload.get("min_count") or 2)):
            name, email = split_sender(str(row.get("sender") or ""))
            if not email or self.person_by_email(email):
                continue
            self.create("person", {"display_name": name or email, "emails": [email], "source": "mail", "notes": f"{row.get('n')} mails reçus"})
            created += 1
        return {"created": created}

    def record_interaction(self, kind: str, *, person_id: int | None = None, organization_id: int | None = None, summary: str = "", ref_kind: str = "", ref_id: Any = "", at: Any = None, actor: str | None = None) -> None:
        with db.connect(self.kernel.dsn) as conn:
            if ref_kind and ref_id:
                exists = db.scalar(conn, "SELECT 1 FROM interactions WHERE ref_kind = %s AND ref_id = %s AND person_id IS NOT DISTINCT FROM %s AND organization_id IS NOT DISTINCT FROM %s", (ref_kind, str(ref_id), person_id, organization_id))
                if exists:
                    return
            db.execute(conn, "INSERT INTO interactions (org_id, kind, person_id, organization_id, at, summary, ref_kind, ref_id, created_by) VALUES (%s, %s, %s, %s, COALESCE(%s::timestamptz, now()), %s, %s, %s, %s)", (self.kernel.org_id, kind, person_id, organization_id, at or None, summary[:400], ref_kind, str(ref_id or ""), actor))

    # --- affiliations, 360 -------------------------------------------------------------------------------------

    def affiliate(self, person_id: int, organization_id: int, role: str = "") -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "INSERT INTO affiliations (person_id, organization_id, role) VALUES (%s, %s, %s) ON CONFLICT (person_id, organization_id, role) DO UPDATE SET role = EXCLUDED.role RETURNING *", (person_id, organization_id, role))
        self.kernel.links.link("person", person_id, "organization", organization_id, role=role or "member")
        return db.jsonable(row) or {}

    def unaffiliate(self, affiliation_id: int) -> bool:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "DELETE FROM affiliations WHERE id = %s RETURNING *", (affiliation_id,))
        if row:
            self.kernel.links.unlink("person", row["person_id"], "organization", row["organization_id"], role=row["role"] or "member")
        return bool(row)

    def view_360(self, kind: str, ident: int) -> dict[str, Any] | None:
        table = self.tables[kind]
        row = table.get(ident)
        if not row:
            return None
        if row.get("merged_into"):
            return {"merged_into": row["merged_into"], "kind": kind}
        k = self.kernel
        links = k.links.of(kind, ident)
        summaries = k.registry.summaries([(str(l["other_kind"]), str(l["other_id"])) for l in links])
        for link in links:
            link["other"] = summaries.get(f"{link['other_kind']}:{link['other_id']}")
        out: dict[str, Any] = {"kind": kind, "entity": row, "links": links, "actions": k.actions.recent(20, entity_kind=kind, entity_id=str(ident))}
        with db.connect(k.dsn) as conn:
            if kind == "person":
                out["affiliations"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT a.*, o.name AS organization_name, o.kind AS organization_kind FROM affiliations a JOIN organizations o ON o.id = a.organization_id WHERE a.person_id = %s ORDER BY a.since DESC NULLS LAST", (ident,))]
                out["interactions"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT * FROM interactions WHERE person_id = %s ORDER BY at DESC LIMIT 50", (ident,))]
            elif kind == "organization":
                out["members"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT a.*, p.display_name, p.emails FROM affiliations a JOIN people p ON p.id = a.person_id WHERE a.organization_id = %s AND p.merged_into IS NULL ORDER BY p.display_name", (ident,))]
                out["interactions"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT * FROM interactions WHERE organization_id = %s ORDER BY at DESC LIMIT 50", (ident,))]
                out["events"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT id, title, starts_at, venue_name FROM outlive_events WHERE (outlive_events.organizer_id <> '' AND outlive_events.organizer_id = %s) AND gone_at IS NULL ORDER BY starts_at DESC LIMIT 30", (row.get("outlive_organizer_id") or "",))]
            else:
                out["events"] = [db.jsonable(r) for r in db.fetch_all(conn, "SELECT id, title, starts_at, organizer_name FROM outlive_events WHERE venue_id <> '' AND venue_id = %s AND gone_at IS NULL ORDER BY starts_at DESC LIMIT 30", (row.get("outlive_location_id") or "",))]
        mail = k.modules.get("mail")
        if mail is not None and kind == "person":
            out["mails"] = [{"id": m["id"], "subject": m["subject"], "mailed_at": m.get("mailed_at"), "category": m["category"], "status": m["status"]} for email in (row.get("emails") or []) for m in mail.store.items_from(email, 10)]
        return out

    # --- import / export ---------------------------------------------------------------------------------------------

    def import_vcards(self, text: str) -> dict[str, int]:
        import vobject

        created = updated = 0
        for card in vobject.readComponents(text):
            if card.name != "VCARD":
                continue
            uid = str(getattr(card, "uid", None).value) if hasattr(card, "uid") else ""
            name = str(card.fn.value) if hasattr(card, "fn") else ""
            emails = sorted({norm_email(e.value) for e in card.contents.get("email", []) if norm_email(e.value)})
            phones = [str(t.value) for t in card.contents.get("tel", [])]
            org = str(card.org.value[0]) if hasattr(card, "org") and card.org.value else ""
            title = str(card.title.value) if hasattr(card, "title") else ""
            first = last = ""
            if hasattr(card, "n"):
                first, last = str(card.n.value.given or ""), str(card.n.value.family or "")
            existing = None
            if uid:
                rows = self.people.list("carddav_uid = %s", [uid], 1)
                existing = rows[0] if rows else None
            if existing is None:
                for email in emails:
                    existing = self.person_by_email(email)
                    if existing:
                        break
            payload = {"display_name": name or " ".join(p for p in [first, last] if p) or (emails[0] if emails else "Sans nom"), "first_name": first, "last_name": last, "emails": emails, "phones": phones, "job_title": title, "source": "carddav", "carddav_uid": uid}
            if existing:
                merged_emails = sorted(set(existing.get("emails") or []) | set(emails))
                self.update("person", existing["id"], {**payload, "emails": merged_emails, "phones": list({*(existing.get("phones") or []), *phones})})
                person_id = int(existing["id"])
                updated += 1
            else:
                person_id = int(self.create("person", payload)["id"])
                created += 1
            if org:
                organization = self.organization_by_outlive("", org)
                if organization is None:
                    organization = self.create("organization", {"name": org, "kind": "autre"})
                self.affiliate(person_id, int(organization["id"]), title)
        return {"created": created, "updated": updated}

    def export_vcards(self) -> str:
        import vobject

        out = []
        for person in self.people.list("merged_into IS NULL", [], 5000):
            card = vobject.vCard()
            card.add("fn").value = person["display_name"]
            card.add("n").value = vobject.vcard.Name(family=person.get("last_name") or "", given=person.get("first_name") or "")
            card.add("uid").value = person.get("carddav_uid") or f"regie-person-{person['id']}@orleans.radiocampus.org"
            for email in person.get("emails") or []:
                card.add("email").value = email
            for phone in person.get("phones") or []:
                card.add("tel").value = phone
            if person.get("job_title"):
                card.add("title").value = person["job_title"]
            out.append(card.serialize())
        return "".join(out)

    def export_csv(self, kind: str = "person") -> str:
        table = self.tables[kind]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        rows = table.list("merged_into IS NULL", [], 10000)
        columns = [c for c in table.columns if c != "merged_into"]
        writer.writerow(["id", *columns])
        for row in rows:
            writer.writerow([row["id"], *[";".join(row[c]) if isinstance(row.get(c), list) else (row.get(c) if row.get(c) is not None else "") for c in columns]])
        return buffer.getvalue()

    def erase_person(self, person_id: int) -> bool:
        """Effacement RGPD : la fiche, ses interactions, ses liens ; les mails restent (ils sont la boîte, pas la fiche)."""
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "DELETE FROM interactions WHERE person_id = %s", (person_id,))
        return self.delete("person", person_id)

    def export_person(self, person_id: int) -> dict[str, Any] | None:
        view = self.view_360("person", person_id)
        return view
