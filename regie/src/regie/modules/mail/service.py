"""Service du module Mails : relevé, index, drapeaux, récap, pièces, Notion et actions IMAP réversibles.

Port du Worker d'Inbox Zero sur les contrats du noyau : jobs persistants, journal d'actions, registre, recherche, outbox.
"""

from __future__ import annotations

import logging
import secrets as pysecrets
import threading
from pathlib import Path
from typing import Any

from regie.connectors.imap import FLAGGED, FOLDER_CHILDREN, FOLDER_PARENT, SEEN, FetchedMail, MailSession, since_criteria
from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.connectors import Connector
from regie.kernel.core import Kernel
from regie.kernel.jobs import P_SCAN, P_USER, JobContext
from regie.kernel.registry import EntityKind
from regie.modules.mail import notion
from regie.modules.mail.attachments import append_excerpt, cache_path, dump_list, inspect, parse_list, payload_for_n, prune_cache, upsert_row
from regie.modules.mail.classify import (
    HAIKU_BATCH_SIZE,
    HAIKU_EXCERPT,
    brief_payload,
    brief_with_anthropic,
    classify_batch_haiku,
    classify_with_anthropic,
    clip,
    heuristic_classification,
    heuristic_gate,
    should_escalate,
)
from regie.modules.mail.sanitize import text as sanitize_text
from regie.modules.mail.serialize import serialize_item
from regie.modules.mail.store import SIGNATURE_MAX, MailStore
from regie.modules.mail.threads import cluster_groups, item_needs_brief, short_summary as thread_summary
from regie.modules.mail.ui import sender_email

log = logging.getLogger("regie.mail")

SCAN_DAYS = 30
SCAN_CHUNK = 10
SCAN_BUDGET_S = 25.0
INDEX_CHUNK = 40
INDEX_BUDGET_S = 10.0
PREBRIEF = 5
FLAG_KINDS = {"seen": (SEEN, True), "unseen": (SEEN, False), "flag": (FLAGGED, True), "unflag": (FLAGGED, False)}
IMAP_KINDS = {"seen", "unseen", "flag", "unflag", "archive", "restore"}
INVERSE = {"seen": "unseen", "unseen": "seen", "flag": "unflag", "unflag": "flag", "archive": "restore", "restore": "archive", "later": "unlater", "unlater": "later"}
ACTION_LABELS = {
    "seen": "Marqué lu",
    "unseen": "Marqué non lu",
    "flag": "Drapeau posé",
    "unflag": "Drapeau retiré",
    "archive": "Archivé",
    "restore": "Remis dans INBOX",
    "later": "Plus tard",
    "unlater": "Remis à trier",
}
NOT_UNDOABLE = {"restore", "unlater"}


def empty_scan_stats() -> dict[str, int]:
    return {"haiku_calls": 0, "sonnet_calls": 0, "auto_moved": 0, "input_tokens": 0, "output_tokens": 0}


def _chunks(seq: list[Any], size: int) -> list[list[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class ImapConnector(Connector):
    system = "imap"
    label = "Boîte mail (IMAP)"

    def __init__(self, service: "MailService") -> None:
        self.service = service

    def configured(self) -> bool:
        return self.service.session is not None

    def health(self) -> dict[str, Any]:
        session = self.service.session
        return session.snapshot() if session else {"ok": False, "error": "IMAP non configuré"}


class MailService:
    name = "mail"

    def __init__(self, kernel: Kernel, session: MailSession | None = None) -> None:
        self.kernel = kernel
        self.settings = kernel.settings
        self.store = MailStore(kernel.dsn, kernel.org_id)
        self.bus = kernel.bus
        self.jobs = kernel.jobs
        if session is not None:
            self.session: MailSession | None = session
        elif self.settings.imap_ready:
            self.session = MailSession(self.settings.imap_host, self.settings.imap_port, self.settings.imap_user, self.settings.imap_password)
        else:
            self.session = None
        self._stats_lock = threading.Lock()
        self.last_error: str | None = None
        self.last_scan_at: str | None = None
        self.last_scan_count = 0
        self.last_scan_stats = empty_scan_stats()
        self.last_index: dict[str, int] = {"indexed": 0, "inbox": 0, "added": 0}
        self.last_index_error: str | None = None
        self.imap_ok: bool | None = None
        self._register()

    # --- déclaration au noyau -----------------------------------------------------------

    def _register(self) -> None:
        k = self.kernel
        k.registry.register(
            EntityKind(
                kind="mail",
                module="mail",
                label="Mail",
                label_plural="Mails",
                icon="mail",
                table="mail_items",
                fetch=self.store.get_items,
                summarize=lambda r: {"title": r.get("subject") or "(sans objet)", "subtitle": r.get("sender") or "", "category": r.get("category"), "status": r.get("status")},
                url=lambda i: f"/mails?id={i}",
                actions=[f"mail.{kind}" for kind in ACTION_LABELS],
            )
        )
        k.registry.register(
            EntityKind(
                kind="mail_index",
                module="mail",
                label="Mail (archive)",
                label_plural="Mails (archive)",
                icon="mail",
                table="mail_index",
                fetch=lambda ids: self.store.index_rows([int(i) for i in ids if str(i).isdigit()]),
                summarize=lambda r: {"title": r.get("subject") or "(sans objet)", "subtitle": r.get("sender") or "", "item_id": r.get("item_id")},
                url=lambda i: f"/mails?mail={i}",
            )
        )
        k.jobs.register("mail.scan", self.job_scan, "imap")
        k.jobs.register("mail.index", self.job_index, "imap")
        k.jobs.register("mail.flags", self.job_flags, "imap")
        k.jobs.register("mail.attachment", self.job_attachment, "imap")
        k.jobs.register("mail.brief", self.job_brief, "llm")
        k.jobs.register("mail.notion", self.job_notion, "llm")
        for kind, label in ACTION_LABELS.items():
            k.actions.register(
                ActionSpec(
                    kind=f"mail.{kind}",
                    module="mail",
                    entity_kind="mail",
                    label=label,
                    apply=self._make_apply(kind),
                    revert=self._revert,
                    snapshot=self._snapshot,
                    prepare=self._make_prepare(kind),
                    lane="imap" if kind in IMAP_KINDS else None,
                    undoable=kind not in NOT_UNDOABLE,
                    inverse=f"mail.{INVERSE[kind]}",
                )
            )
        k.connectors.register(ImapConnector(self))
        poll = max(60, int(self.settings.mail_poll_seconds))
        if self.session is not None:
            k.scheduler.ensure("mail.scan", poll, priority=P_SCAN)
            k.scheduler.ensure("mail.index", poll, priority=P_SCAN + 1)
            k.scheduler.ensure("mail.flags", 60, priority=P_SCAN)

    def stop(self) -> None:
        if self.session:
            self.session.close()

    # --- réglages du module -----------------------------------------------------------------

    def setting(self, key: str, default: str = "") -> str:
        return self.kernel.setting(f"mail.{key}", default)

    def set_setting(self, key: str, value: str) -> None:
        self.kernel.set_setting(f"mail.{key}", value)

    @property
    def signature(self) -> str:
        return self.setting("signature")

    def notion_token(self) -> str:
        stored = ""
        try:
            stored = self.kernel.secrets.get("notion", "token")
        except Exception:
            stored = ""
        return self.settings.notion_secret(stored)

    def feed_token(self) -> str:
        token = self.kernel.setting("feed_token")
        if not token:
            token = pysecrets.token_urlsafe(24)
            self.kernel.set_setting("feed_token", token)
        return token

    def settings_view(self) -> dict[str, Any]:
        return {"imap_ready": self.settings.imap_ready, "llm_ready": self.settings.llm_ready, "notion_ready": bool(self.notion_token())}

    def status(self) -> dict[str, Any]:
        return {
            "imap_ok": self.imap_ok,
            "last_error": self.last_error,
            "last_scan_at": self.last_scan_at,
            "last_scan_count": self.last_scan_count,
            "last_scan_stats": dict(self.last_scan_stats),
            "last_index": dict(self.last_index),
            "last_index_error": self.last_index_error,
            "session": self.session.snapshot() if self.session else None,
        }

    def incident(self) -> str | None:
        if self.last_error:
            return self.last_error
        if self.session is None:
            return "IMAP non configuré"
        if self.imap_ok is False:
            return "IMAP à vérifier"
        return None

    def _add_usage(self, usage: dict[str, int] | None, kind: str | None = None) -> None:
        usage = usage or {}
        with self._stats_lock:
            self.last_scan_stats["input_tokens"] += int(usage.get("input_tokens") or 0)
            self.last_scan_stats["output_tokens"] += int(usage.get("output_tokens") or 0)
            if kind:
                key = f"{kind}_calls"
                self.last_scan_stats[key] = int(self.last_scan_stats.get(key) or 0) + 1

    # --- recherche / publication -------------------------------------------------------------------

    def _index_rows_for_search(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.kernel.search.index(
                "mail_index",
                row["id"],
                title=row.get("subject") or "(sans objet)",
                subtitle=row.get("sender") or "",
                body=(row.get("body") or "")[:12000],
                url=f"/mails?mail={row['id']}",
                meta={"item_id": row.get("item_id"), "mailed_at": row.get("mailed_at") or ""},
            )

    def _index_item(self, item_id: int, payload: dict[str, Any]) -> None:
        rows = self.store.upsert_index([{**payload, "body": payload.get("excerpt") or "", "id": item_id}])
        self._index_rows_for_search(rows)

    def publish_item(self, item_id: int, reason: str) -> None:
        stored = self.store.get_item(item_id)
        if stored:
            self.bus.publish("item", {"item": serialize_item(stored, self.signature), "reason": reason})

    # --- relevé --------------------------------------------------------------------------------------

    def scan(self) -> dict[str, Any]:
        try:
            return self.jobs.run_inline("mail.scan", {})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "created": 0}

    def job_scan(self, ctx: JobContext) -> dict[str, Any]:
        if self.session is None:
            self.imap_ok = False
            self.last_error = "IMAP non configuré"
            return {"ok": False, "error": self.last_error, "created": 0}
        manual = bool(ctx.payload.get("manual"))
        if manual or not ctx.payload.get("resume"):
            with self._stats_lock:
                self.last_scan_stats = empty_scan_stats()
        self.bus.publish("scan", {"status": "running"})
        try:
            validity = self.session.uidvalidity()
            prev_validity = self.setting("scan_uidvalidity")
            try:
                last_uid = int(self.setting("scan_last_uid") or 0) if prev_validity == validity else 0
            except ValueError:
                last_uid = 0
            criteria = since_criteria(SCAN_DAYS)
            if last_uid:
                criteria = f"UID {last_uid + 1}:* {criteria}"
            found = [uid for uid in self.session.search_uids(criteria) if uid.isdigit() and int(uid) > last_uid]
            uids_asc = sorted(found, key=int)
            known = self.store.items_by_uids(validity, uids_asc)
            extra = self.setting("extra_prompt")
            thread_rows = self.store.list_items(statuses=["proposed", "moved"], limit=120)
            created = retried = 0
            chunks = _chunks(uids_asc, SCAN_CHUNK)
            for index, chunk in enumerate(chunks):
                ctx.progress(f"Relevé {index + 1}/{len(chunks)}")
                pending_uids = [uid for uid in chunk if uid not in known or (known[uid].get("status") == "proposed" and known[uid].get("error"))]
                if pending_uids:
                    mails = self.session.fetch_mails(pending_uids, body_chars=self.settings.mail_body_chars, validity=validity)
                    pending = []
                    for mail in mails:
                        mail.excerpt = sanitize_text(mail.excerpt)
                        pending.append((mail, known.get(mail.uid)))
                    c, r = self._store_classified(pending, extra, validity, thread_rows)
                    created += c
                    retried += r
                self.set_setting("scan_last_uid", str(max(int(uid) for uid in chunk)))
                self.set_setting("scan_uidvalidity", validity)
                if index + 1 < len(chunks) and ctx.should_yield(P_USER, budget_s=SCAN_BUDGET_S):
                    self.bus.publish("queue", {"reason": "scan"})
                    return {"ok": True, "continue": True, "created": created, "retried": retried, "seen": len(uids_asc)}
            self.imap_ok = True
            self.last_scan_count = created
            self.last_scan_at = db.iso(db.utcnow())
            self.last_error = None
            self.kernel.connectors.state.ok("imap", meta={"last_scan_at": self.last_scan_at})
            if created or retried:
                self.bus.publish("queue", {"reason": "scan", "created": created})
                self.kernel.outbox.emit("mail.scanned", {"created": created, "retried": retried})
            self._prebrief()
            stats = dict(self.last_scan_stats)
            self.bus.publish("scan", {"status": "done", "created": created, "seen": len(uids_asc), "manual": manual})
            return {"ok": True, "created": created, "retried": retried, "auto_moved": 0, "seen": len(uids_asc), "haiku_calls": stats.get("haiku_calls", 0), "sonnet_calls": stats.get("sonnet_calls", 0)}
        except Exception as exc:
            self.imap_ok = False
            self.last_error = str(exc)[:300]
            self.kernel.connectors.state.error("imap", self.last_error)
            self.bus.publish("scan", {"status": "failed", "error": self.last_error})
            raise

    def _store_classified(self, pending: list[tuple[FetchedMail, dict[str, Any] | None]], extra: str, validity: str, thread_rows: list[dict[str, Any]]) -> tuple[int, int]:
        created = retried = 0
        for mail, existing, result in self._classify_pending(pending, extra, thread_rows):
            error = result.pop("error", None)
            draft = result.pop("draft", "") or ""
            payload = {
                "imap_uid": mail.uid,
                "uidvalidity": validity,
                "message_id": mail.message_id,
                "in_reply_to": mail.in_reply_to,
                "sender": mail.sender,
                "subject": mail.subject,
                "excerpt": mail.excerpt,
                "category": result["category"],
                "reason": result.get("reason") or "",
                "summary": sanitize_text(result.get("summary") or thread_summary(mail.excerpt, result.get("reason") or "")),
                "confidence": result.get("confidence") or 0,
                "status": "proposed",
                "error": error,
                "draft": sanitize_text(draft),
                "mailed_at": mail.mailed_at,
                "attachments": dump_list(mail.attachments),
                "seen": mail.seen,
                "flagged": mail.flagged,
            }
            if existing:
                item_id = int(existing["id"])
                self.store.update_item(item_id, payload)
                self.store.set_flags([item_id], seen=mail.seen, flagged=mail.flagged)
                retried += 1
            else:
                item_id = self.store.insert_item(payload)
                thread_rows.append({**payload, "id": item_id})
                created += 1
                self.kernel.outbox.emit("mail.received", {"id": item_id, "sender": mail.sender, "subject": mail.subject, "category": result["category"]})
            self._index_item(item_id, payload)
            if error:
                self.last_error = str(error)
        return created, retried

    def _classify_pending(self, pending, extra: str, thread_rows=None):
        settings = self.settings
        cheap: list[tuple[FetchedMail, dict[str, Any] | None, dict[str, Any]]] = []
        need_llm: list[tuple[FetchedMail, dict[str, Any] | None]] = []
        for mail, existing in pending:
            result = heuristic_gate(mail.sender, mail.subject, mail.excerpt, mail.headers)
            if result:
                cheap.append((mail, existing, result))
                continue
            remembered = self.store.memory_lookup(sender_email(mail.sender))
            if remembered:
                if not remembered.get("summary"):
                    remembered["summary"] = thread_summary(mail.excerpt, remembered.get("reason") or "")
                cheap.append((mail, existing, remembered))
                continue
            inherited = self.store.category_for_thread(mail.subject, skip_uid=mail.uid, rows=thread_rows)
            if inherited:
                cheap.append((mail, existing, inherited))
                continue
            need_llm.append((mail, existing))
        if not settings.llm_ready:
            out = list(cheap)
            for mail, existing in need_llm:
                out.append((mail, existing, heuristic_classification(mail.sender, mail.subject, mail.excerpt, mail.headers)))
            return out
        haiku_done: list[tuple[FetchedMail, dict[str, Any] | None, dict[str, Any]]] = []
        escalate: list[tuple[FetchedMail, dict[str, Any] | None]] = []
        for start in range(0, len(need_llm), HAIKU_BATCH_SIZE):
            chunk = need_llm[start : start + HAIKU_BATCH_SIZE]
            try:
                packed = [{"sender": m.sender, "subject": m.subject, "excerpt": clip(m.excerpt, HAIKU_EXCERPT)} for m, _e in chunk]
                parsed, usage = classify_batch_haiku(api_key=settings.anthropic_api_key, model=settings.anthropic_fast_model, mails=packed, extra_prompt=extra, workspace_id=settings.anthropic_workspace_id)
                self._add_usage(usage, "haiku")
                for (mail, existing), result in zip(chunk, parsed):
                    if should_escalate(result, mail.subject, mail.excerpt):
                        escalate.append((mail, existing))
                    else:
                        haiku_done.append((mail, existing, result))
            except Exception as exc:
                log.warning("lot haiku : %s", exc)
                for mail, existing in chunk:
                    guess = heuristic_classification(mail.sender, mail.subject, mail.excerpt, mail.headers)
                    if should_escalate(guess, mail.subject, mail.excerpt):
                        escalate.append((mail, existing))
                    else:
                        haiku_done.append((mail, existing, guess))
        out = list(cheap) + haiku_done
        for mail, existing in escalate:
            try:
                result = classify_with_anthropic(api_key=settings.anthropic_api_key, model=settings.anthropic_model, sender=mail.sender, subject=mail.subject, excerpt=mail.excerpt, extra_prompt=extra, workspace_id=settings.anthropic_workspace_id)
                self._add_usage(result.pop("_usage", None), "sonnet")
            except Exception as exc:
                result = {"category": "read", "reason": "classification impossible", "summary": thread_summary(mail.excerpt), "confidence": 0, "error": str(exc)[:400], "via": "error"}
            out.append((mail, existing, result))
        return out

    def _prebrief(self) -> None:
        if not self.settings.llm_ready:
            return
        picked = 0
        for row in self.store.list_items(status="proposed", limit=40):
            if row.get("category") not in {"todo", "waiting"} or not item_needs_brief(row):
                continue
            self.jobs.submit("mail.brief", {"item_id": int(row["id"]), "auto": True}, P_SCAN, dedupe=True)
            picked += 1
            if picked >= PREBRIEF:
                break

    # --- index ---------------------------------------------------------------------------------------

    def index_inbox(self) -> dict[str, Any]:
        return self.jobs.run_inline("mail.index", {})

    def job_index(self, ctx: JobContext) -> dict[str, Any]:
        self._index_rows_for_search(self.store.seed_index_from_items())
        stats = {"indexed": self.store.index_count(), "inbox": int(self.setting("index_inbox") or 0), "added": 0}
        self.last_index = stats
        if self.session is None:
            return stats
        try:
            validity = self.session.uidvalidity()
            prev = self.setting("index_uidvalidity")
            if prev and prev != validity:
                self.store.clear_index()
                self._index_rows_for_search(self.store.seed_index_from_items())
            self.set_setting("index_uidvalidity", validity)
            total = int(self.session.status("INBOX").get("MESSAGES") or 0)
            self.set_setting("index_inbox", str(total))
            all_uids = self.session.search_uids("ALL")
            gone = self.store.mark_gone(validity, set(all_uids))
            if gone:
                self.bus.publish("queue", {"reason": "gone", "ids": gone})
            have = self.store.indexed_uids(validity, "INBOX")
            missing = [uid for uid in all_uids if uid not in have]
            added = 0
            chunks = _chunks(missing, INDEX_CHUNK)
            remaining = False
            for index, chunk in enumerate(chunks):
                mails = self.session.fetch_mails(chunk, body_chars=self.settings.mail_body_chars, validity=validity)
                known = self.store.items_by_uids(validity, [m.uid for m in mails])
                rows = []
                for mail in mails:
                    hit = known.get(mail.uid)
                    rows.append({"imap_uid": mail.uid, "uidvalidity": validity, "message_id": mail.message_id, "sender": mail.sender, "subject": mail.subject, "body": sanitize_text(mail.excerpt), "summary": (hit or {}).get("summary") or "", "mailed_at": mail.mailed_at, "id": hit["id"] if hit else None})
                self._index_rows_for_search(self.store.upsert_index(rows))
                added += len(rows)
                self.last_index = {"indexed": self.store.index_count(), "inbox": total, "added": added}
                if index + 1 < len(chunks) and ctx.should_yield(P_USER, budget_s=INDEX_BUDGET_S):
                    remaining = True
                    break
                if index % 3 == 2:
                    self.bus.publish("index", {**self.last_index, "running": True})
            stats = {"indexed": self.store.index_count(), "inbox": total, "added": added}
            self.last_index = stats
            self.last_index_error = None
            self.bus.publish("index", {**stats, "running": remaining})
            return {**stats, "continue": remaining}
        except Exception as exc:
            self.last_index_error = str(exc)[:300]
            raise

    # --- drapeaux ----------------------------------------------------------------------------------------

    def job_flags(self, ctx: JobContext) -> dict[str, Any]:
        del ctx
        if self.session is None:
            return {"changed": 0}
        validity = self.session.uidvalidity()
        by_uid = self.store.inbox_uids_for_status(validity)
        if not by_uid:
            return {"changed": 0}
        flags = self.session.fetch_flags(list(by_uid))
        changed = self.store.sync_flags(validity, flags)
        missing = [by_uid[uid] for uid in by_uid if uid not in flags]
        if changed or missing:
            self.bus.publish("queue", {"reason": "flags", "changed": changed})
        self.imap_ok = True
        return {"changed": changed, "checked": len(by_uid)}

    # --- récap -------------------------------------------------------------------------------------------

    def brief(self, item_id: int) -> dict[str, Any]:
        self.jobs.run_inline("mail.brief", {"item_id": int(item_id)})
        item = self.store.get_item(int(item_id))
        if not item:
            raise KeyError("proposition introuvable")
        return item

    def job_brief(self, ctx: JobContext) -> dict[str, Any]:
        item_id = int(ctx.payload.get("item_id") or 0)
        settings = self.settings
        if not settings.llm_ready:
            raise RuntimeError("Claude non configuré")
        item = self.store.get_item(item_id)
        if not item:
            raise KeyError("proposition introuvable")
        if ctx.payload.get("auto") and not item_needs_brief(item):
            return {"skipped": True}
        ctx.progress("Récap en cours")
        extra = self.setting("extra_prompt")
        rows = self.store.list_items(statuses=["proposed", "moved", "skipped"], limit=80)
        if not any(int(row["id"]) == item_id for row in rows):
            rows.append(item)
        thread = next((group for group in cluster_groups(rows) if any(int(row["id"]) == item_id for row in group)), [item])
        thread.sort(key=lambda row: (row.get("mailed_at") or row.get("created_at") or "", row["id"]))
        messages = [brief_payload(row.get("sender") or "", row.get("subject") or "", sanitize_text(row.get("excerpt") or ""), row.get("attachments")) for row in thread[-8:]]
        out, usage = brief_with_anthropic(api_key=settings.anthropic_api_key, model=settings.anthropic_model, messages=messages, subject=item.get("subject") or "", extra_prompt=extra, workspace_id=settings.anthropic_workspace_id, effort=settings.anthropic_effort)
        self._add_usage(usage, "sonnet")
        summary = sanitize_text((out.get("summary") or "").strip() or thread_summary(item.get("excerpt") or "", item.get("reason") or ""))
        self.store.set_summary(item_id, summary)
        draft = sanitize_text(out.get("draft") or "")
        from regie.modules.mail.verify import check_draft, source_blob

        source = source_blob({**item, "summary": summary, "excerpt": sanitize_text(item.get("excerpt") or "")})
        if check_draft(draft, source, self.signature):
            self.store.set_draft(item_id, "")
            self.store.set_status(item_id, item.get("status") or "proposed", error="brouillon écarté : donnée absente")
        else:
            self.store.set_draft(item_id, draft)
            if (item.get("error") or "").startswith("brouillon écarté"):
                self.store.set_status(item_id, item.get("status") or "proposed", error=None)
        for row in thread:
            if int(row["id"]) != item_id and (row.get("status") or "") == "proposed":
                self.store.set_draft(int(row["id"]), "")
        updated = self.store.get_item(item_id)
        if not updated:
            raise KeyError("proposition introuvable")
        packed = serialize_item(updated, self.signature)
        self.bus.publish("item", {"item": packed, "reason": "brief"})
        return {"item": packed}

    # --- pièces jointes -------------------------------------------------------------------------------------

    def job_attachment(self, ctx: JobContext) -> dict[str, Any]:
        item_id = int(ctx.payload.get("item_id") or 0)
        n = int(ctx.payload.get("n") or 0)
        item = self.store.get_item(item_id)
        if not item:
            raise ValueError("mail introuvable")
        path, mime, filename = self.fetch_attachment(item, n)
        return {"path": str(path), "mime": mime, "filename": filename}

    def fetch_attachment(self, item: dict[str, Any], n: int) -> tuple[Path, str, str]:
        rows = parse_list(item.get("attachments"))
        row = next((entry for entry in rows if int(entry.get("n", -1)) == n), None)
        if row is None:
            raise ValueError("pièce introuvable")
        kind = str(row.get("kind") or "other")
        if kind not in {"pdf", "image", "audio"}:
            raise ValueError("type de pièce non pris en charge")
        filename = str(row.get("filename") or f"piece-{n}")
        path = cache_path(self.settings.data_dir, int(item["id"]), n, filename)
        if path.is_file() and str(row.get("status") or "") == "ok":
            return path, str(row.get("mime") or row.get("content_type") or "application/octet-stream"), filename
        if self.session is None:
            raise ValueError("IMAP non configuré")
        folder = str(item.get("folder") or "INBOX")
        uid = str(item.get("folder_uid") or "") if folder != "INBOX" else str(item.get("imap_uid") or "")
        msg = self.session.fetch_message(uid or str(item.get("imap_uid") or ""), folder) if uid else None
        if msg is None and folder != "INBOX":
            msg = self.session.fetch_message(str(item.get("imap_uid") or ""), "INBOX")
        if msg is None:
            raise ValueError("mail introuvable sur IMAP")
        payload = payload_for_n(msg, n)
        result = inspect(payload, filename, str(row.get("content_type") or ""))
        if not result.get("ok"):
            self.store.set_attachments(int(item["id"]), dump_list(upsert_row(rows, n, status="refus", error=result.get("error") or "refusé", text="")))
            self.publish_item(int(item["id"]), "attachment")
            raise ValueError(result.get("error") or "pièce refusée")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        prune_cache(self.settings.data_dir)
        text = str(result.get("text") or "")
        self.store.set_attachments(int(item["id"]), dump_list(upsert_row(rows, n, status="ok", error="", text=text, note=result.get("note") or "", mime=result.get("mime") or "")))
        if text:
            stored = self.store.get_item(int(item["id"])) or item
            self.store.set_summary(int(item["id"]), append_excerpt(stored.get("summary") or "", text))
        self.publish_item(int(item["id"]), "attachment")
        return path, str(result.get("mime") or "application/octet-stream"), filename

    # --- Notion ---------------------------------------------------------------------------------------------

    def job_notion(self, ctx: JobContext) -> dict[str, Any]:
        settings = self.settings
        item_id = int(ctx.payload.get("item_id") or 0)
        note = str(ctx.payload.get("note") or "")
        item = self.store.get_item(item_id)
        if not item:
            raise ValueError("mail introuvable")
        token = self.notion_token()
        if not token:
            raise ValueError("Jeton Notion manquant (Réglages).")
        uploads = self._notion_uploads(ctx, token, item)
        ctx.progress("Création de la tâche")
        item = self.store.get_item(item_id) or item
        created = notion.add_item_to_notion(item=item, note=note, token=token, api_key=settings.anthropic_api_key, model=settings.anthropic_model, extra_prompt=self.setting("extra_prompt"), workspace_id=settings.anthropic_workspace_id, effort=settings.anthropic_effort, database_id=settings.notion_database_id, project_id=settings.notion_project_id, data_source_id=settings.notion_data_source_id, uploads=uploads)
        self.store.set_notion_url(item_id, created["url"])
        stored = self.store.get_item(item_id)
        packed = serialize_item(stored, self.signature) if stored else None
        if packed:
            self.bus.publish("item", {"item": packed, "reason": "notion"})
        return {"url": created["url"], "title": created.get("title") or "", "item": packed}

    def _thread_rows(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.store.list_items(statuses=["proposed", "moved", "skipped"], limit=80)
        item_id = int(item["id"])
        if not any(int(row["id"]) == item_id for row in rows):
            rows.append(item)
        for group in cluster_groups(rows):
            if any(int(row["id"]) == item_id for row in group):
                return group
        return [item]

    def _notion_uploads(self, ctx: JobContext, token: str, item: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()
        todo: list[tuple[dict[str, Any], dict[str, Any], int]] = []
        for row in self._thread_rows(item):
            for att in parse_list(row.get("attachments")):
                if str(att.get("kind") or "") not in {"pdf", "image", "audio"}:
                    continue
                try:
                    n = int(att.get("n", -1))
                except (TypeError, ValueError):
                    continue
                key = (int(row["id"]), n)
                if key in seen:
                    continue
                seen.add(key)
                todo.append((row, att, n))
                if len(todo) >= 12:
                    break
        for index, (row, att, n) in enumerate(todo):
            filename = str(att.get("filename") or f"piece-{n}")
            ctx.progress(f"Ajout des pièces {index + 1}/{len(todo)}")
            packed = {"kind": str(att.get("kind") or ""), "filename": filename, "file_upload_id": ""}
            try:
                fetched = self.jobs.call("mail.attachment", {"item_id": int(row["id"]), "n": n}, P_USER, timeout=120)
                packed["filename"] = fetched.get("filename") or filename
                packed["file_upload_id"] = notion.upload_file(token, Path(fetched["path"]), packed["filename"], fetched.get("mime") or "")
            except Exception as exc:
                log.warning("Notion pièce %s : %s", filename, exc)
            out.append(packed)
        return out

    # --- actions ------------------------------------------------------------------------------------------------

    def _snapshot(self, ids: list[str]) -> dict[str, Any]:
        items = self.store.get_items(ids)
        return {
            str(item["id"]): {
                "seen": bool(item.get("seen", 1)),
                "flagged": bool(item.get("flagged", 0)),
                "folder": item.get("folder") or "INBOX",
                "folder_uid": item.get("folder_uid") or "",
                "imap_uid": item.get("imap_uid") or "",
                "status": item.get("status") or "proposed",
                "category": item.get("category") or "",
                "message_id": item.get("message_id") or "",
                "sender": item.get("sender") or "",
            }
            for item in items
        }

    def _make_prepare(self, kind: str):
        def prepare(ctx: ActionCtx) -> None:
            ids = [int(i) for i in ctx.ids if str(i).isdigit()]
            if kind in IMAP_KINDS and self.session is None:
                raise PermissionError("IMAP non configuré")
            if kind == "archive" and self.session is not None and self.session.connected and not self.session.snapshot().get("can_move"):
                raise PermissionError("Le serveur IMAP ne sait pas déplacer (ni MOVE ni UIDPLUS).")
            if not ids:
                raise ValueError("aucun mail")
            self.apply_local(kind, ids)
            if kind == "later":
                for snap in ctx.before.values():
                    self.store.memory_forget(str(snap.get("sender") or ""))
            if kind not in IMAP_KINDS:
                self.bus.publish("queue", {"reason": "action", "kind": kind})

        return prepare

    def apply_local(self, kind: str, ids: list[int]) -> None:
        if kind in {"seen", "unseen"}:
            self.store.set_flags(ids, seen=(kind == "seen"))
        elif kind in {"flag", "unflag"}:
            self.store.set_flags(ids, flagged=(kind == "flag"))
        elif kind == "archive":
            self.store.set_status_many(ids, "moved")
        elif kind == "later":
            self.store.set_status_many(ids, "skipped")
        elif kind in {"unlater", "restore"}:
            self.store.set_status_many(ids, "proposed")

    def _revert(self, ctx: ActionCtx) -> None:
        for key, snap in ctx.before.items():
            if not str(key).isdigit():
                continue
            item_id = int(key)
            self.store.set_flags([item_id], seen=bool(snap.get("seen", True)), flagged=bool(snap.get("flagged", False)))
            self.store.set_status(item_id, str(snap.get("status") or "proposed"))
            self.publish_item(item_id, "action-failed")
        self.bus.publish("queue", {"reason": "action-failed"})

    def _make_apply(self, kind: str):
        def apply(ctx: ActionCtx) -> dict[str, Any]:
            ids = [int(i) for i in ctx.ids if str(i).isdigit()]
            touched: dict[str, Any] = {}
            if kind in IMAP_KINDS:
                if self.session is None:
                    raise RuntimeError("IMAP non configuré")
                touched = self._apply_imap(kind, ids, ctx.params, ctx.job)
            for item_id in ids:
                self.publish_item(item_id, "action")
            self.bus.publish("queue", {"reason": "action", "kind": kind})
            self.kernel.outbox.emit(f"mail.{kind}", {"ids": ids})
            return touched

        return apply

    def _apply_imap(self, kind: str, ids: list[int], params: dict[str, Any], job: JobContext | None) -> dict[str, Any]:
        assert self.session is not None
        touched: dict[str, Any] = {}
        current = {int(row["id"]): row for row in self.store.get_items(ids)}

        def _location(item_id: int) -> tuple[str, str]:
            row = current.get(item_id) or {}
            folder = str(row.get("folder") or "INBOX")
            uid = str(row.get("folder_uid") or "") if folder != "INBOX" else str(row.get("imap_uid") or "")
            return folder, uid

        if kind in FLAG_KINDS:
            flag, value = FLAG_KINDS[kind]
            groups: dict[str, list[str]] = {}
            for item_id in ids:
                folder, uid = _location(item_id)
                if uid:
                    groups.setdefault(folder, []).append(uid)
            for folder, uids in groups.items():
                self.session.store_flag(uids, flag, value, folder)
            if kind in {"seen", "unseen"}:
                self.store.set_flags(ids, seen=value)
            else:
                self.store.set_flags(ids, flagged=value)
            return touched
        if kind == "archive":
            children = self.session.ensure_folders()
            parent, _ = self.session.folder_names()
            per_folder: dict[str, list[int]] = {}
            for item_id in ids:
                row = current.get(item_id) or {}
                if str(row.get("folder") or "INBOX") != "INBOX":
                    continue
                target = str(params.get("folder") or "") or children.get(str(row.get("category") or ""), children.get("read", parent))
                per_folder.setdefault(target, []).append(item_id)
            moved: dict[str, str] = {}
            for folder, group in per_folder.items():
                if job:
                    job.progress(f"Archivage vers {folder.split(self.session.delim)[-1]}")
                uids = [str((current.get(i) or {}).get("imap_uid") or "") for i in group]
                mapping = self.session.move([u for u in uids if u], folder, source="INBOX")
                for item_id in group:
                    row = current.get(item_id) or {}
                    uid = str(row.get("imap_uid") or "")
                    new_uid = mapping.get(uid) or self.session.find_uid_by_message_id(str(row.get("message_id") or ""), folder)
                    self.store.set_folder(item_id, folder, new_uid, status="moved")
                    self.store.memory_remember(str(row.get("sender") or ""), str(row.get("category") or ""))
                    moved[str(item_id)] = f"{folder}:{new_uid}"
            touched["moved"] = moved
            touched["folders"] = sorted(per_folder)
            return touched
        if kind == "restore":
            for item_id in ids:
                row = current.get(item_id) or {}
                folder, folder_uid = _location(item_id)
                if folder == "INBOX":
                    self.store.restore_inbox(item_id, str(row.get("imap_uid") or ""))
                    continue
                if not folder_uid:
                    folder_uid = self.session.find_uid_by_message_id(str(row.get("message_id") or ""), folder)
                if not folder_uid:
                    raise RuntimeError("Mail introuvable dans le dossier d’archive.")
                mapping = self.session.move([folder_uid], "INBOX", source=folder)
                new_uid = mapping.get(folder_uid) or self.session.find_uid_by_message_id(str(row.get("message_id") or ""), "INBOX")
                self.store.restore_inbox(item_id, new_uid or str(row.get("imap_uid") or ""))
            return touched
        raise ValueError(f"action inconnue : {kind}")

    # --- adoption depuis la recherche ------------------------------------------------------------------------------

    def adopt(self, index_id: int, queue: bool) -> dict[str, Any] | None:
        row = self.store.index_get(index_id)
        if not row:
            return None
        item = self.store.get_item(int(row["item_id"])) if row.get("item_id") else None
        if item is None:
            item = self.store.get_item_by_uid(str(row["uidvalidity"]), str(row["imap_uid"]))
        if item is None:
            item_id = self.store.insert_item({"imap_uid": str(row["imap_uid"]), "uidvalidity": str(row["uidvalidity"]), "message_id": row.get("message_id") or "", "sender": row.get("sender") or "", "subject": row.get("subject") or "", "excerpt": row.get("body") or "", "category": "read", "reason": "Ajouté depuis la recherche.", "confidence": 0.5, "status": "proposed" if queue else "skipped", "mailed_at": row.get("mailed_at") or "", "folder": row.get("folder") or "INBOX"})
            item = self.store.get_item(item_id)
            with db.connect(self.kernel.dsn) as conn:
                db.execute(conn, "UPDATE mail_index SET item_id = %s WHERE id = %s", (item_id, index_id))
        elif queue and item.get("status") in {"skipped", "gone"}:
            self.store.set_status(int(item["id"]), "proposed")
            item = self.store.get_item(int(item["id"]))
        self.bus.publish("queue", {"reason": "adopt"})
        return item

    def folders(self) -> dict[str, str]:
        return {key: f"{FOLDER_PARENT}/{name}" for key, name in FOLDER_CHILDREN.items()}
