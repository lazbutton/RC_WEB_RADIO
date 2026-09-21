from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from inboxzero import db, notion
from inboxzero.attachments import (
    append_excerpt,
    cache_path,
    dump_list,
    inspect,
    parse_list,
    payload_for_n,
    prune_cache,
    upsert_row,
)
from inboxzero.classify import (
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
from inboxzero.config import Settings
from inboxzero.imaputil import FLAGGED, SEEN, FetchedMail, MailSession, since_criteria
from inboxzero.jobs import LANE_IMAP, LANE_LLM, P_ACTION, P_SCAN, P_USER, EventBus, JobContext, JobRunner
from inboxzero.sanitize import text as sanitize_text
from inboxzero.serialize import serialize_action, serialize_item
from inboxzero.threads import cluster_groups, item_needs_brief, short_summary as thread_summary
from inboxzero.ui import sender_email
from inboxzero.verify import check_draft, source_blob

log = logging.getLogger("inboxzero.worker")

SCAN_DAYS = 30
SCAN_CHUNK = 10
SCAN_BUDGET_S = 25.0
INDEX_CHUNK = 40
INDEX_BUDGET_S = 10.0
FLAGS_EVERY_S = 60
KEEPALIVE_EVERY_S = 240
PREBRIEF = 5
FLAG_KINDS = {"seen": (SEEN, True), "unseen": (SEEN, False), "flag": (FLAGGED, True), "unflag": (FLAGGED, False)}
INVERSE = {
    "seen": "unseen",
    "unseen": "seen",
    "flag": "unflag",
    "unflag": "flag",
    "archive": "restore",
    "restore": "archive",
    "later": "unlater",
    "unlater": "later",
}
IMAP_KINDS = {"seen", "unseen", "flag", "unflag", "archive", "restore"}
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


def empty_scan_stats() -> dict[str, int]:
    return {"haiku_calls": 0, "sonnet_calls": 0, "auto_moved": 0, "input_tokens": 0, "output_tokens": 0}


def _chunks(seq: list[Any], size: int) -> list[list[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class Worker:
    def __init__(
        self,
        settings: Settings,
        session: MailSession | None = None,
        runner: JobRunner | None = None,
        bus: EventBus | None = None,
        session_factory: Callable[[Settings], MailSession | None] | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus or (runner.bus if runner else EventBus())
        self.runner = runner or JobRunner(settings.db_path, self.bus)
        if session is not None:
            self.session: MailSession | None = session
        elif session_factory is not None:
            self.session = session_factory(settings)
        elif settings.imap_ready:
            self.session = MailSession(settings.imap_host, settings.imap_port, settings.imap_user, settings.imap_password)
        else:
            self.session = None
        self._stats_lock = threading.Lock()
        self.last_error: str | None = None
        self.last_scan_at: str | None = None
        self.last_scan_count: int = 0
        self.last_scan_stats: dict[str, int] = empty_scan_stats()
        self.last_index: dict[str, int] = {"indexed": 0, "inbox": 0, "added": 0}
        self.last_index_error: str | None = None
        self.imap_ok: bool | None = None
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._register()

    # --- lifecycle -----------------------------------------------------------------

    def _register(self) -> None:
        r = self.runner
        r.register("scan", self.job_scan, LANE_IMAP)
        r.register("index", self.job_index, LANE_IMAP)
        r.register("flags", self.job_flags, LANE_IMAP)
        r.register("attachment", self.job_attachment, LANE_IMAP)
        r.register("action", self.job_action, LANE_IMAP)
        r.register("brief", self.job_brief, LANE_LLM)
        r.register("notion", self.job_notion, LANE_LLM)

    def start(self) -> None:
        self.runner.start()
        self.thread = threading.Thread(target=self._tick, name="inboxzero-tick", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.runner.stop()
        if self.session:
            self.session.close()

    def _tick(self) -> None:
        interval = max(60, int(self.settings.inboxzero_poll_seconds))
        last_scan = 0.0
        last_flags = 0.0
        last_alive = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now - last_scan >= interval:
                    last_scan = now
                    self.runner.submit("scan", {}, P_SCAN, dedupe=True)
                    self.runner.submit("index", {}, P_SCAN + 1, dedupe=True)
                elif now - last_flags >= FLAGS_EVERY_S:
                    last_flags = now
                    self.runner.submit("flags", {}, P_SCAN, dedupe=True)
                if self.session and now - last_alive >= KEEPALIVE_EVERY_S:
                    last_alive = now
                    if not self.runner.has_pending(P_SCAN + 1, lane=LANE_IMAP):
                        self.session.noop()
            except Exception as exc:
                log.warning("tick : %s", exc)
            self._stop.wait(5)

    # --- stats ---------------------------------------------------------------------

    def _add_usage(self, usage: dict[str, int] | None, kind: str | None = None) -> None:
        usage = usage or {}
        with self._stats_lock:
            self.last_scan_stats["input_tokens"] += int(usage.get("input_tokens") or 0)
            self.last_scan_stats["output_tokens"] += int(usage.get("output_tokens") or 0)
            if kind:
                key = f"{kind}_calls"
                self.last_scan_stats[key] = int(self.last_scan_stats.get(key) or 0) + 1

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
            "jobs": self.runner.snapshot(),
        }

    # --- convenience (sync) ------------------------------------------------------------

    def scan(self, limit: int = 50) -> dict[str, Any]:
        del limit
        try:
            return self.runner.run_inline("scan", {})
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc), "created": 0}

    def index_inbox(self) -> dict[str, Any]:
        return self.runner.run_inline("index", {})

    def brief(self, item_id: int) -> dict[str, Any]:
        result = self.runner.run_inline("brief", {"item_id": int(item_id)})
        item = db.get_item(self.settings.db_path, int(item_id))
        if not item:
            raise KeyError("proposition introuvable")
        return item if result is not None else item

    def redraft(self, item_id: int) -> str:
        return self.brief(item_id).get("draft") or ""

    def confirm(self, item_id: int) -> None:
        raise RuntimeError("Utilise une action : archive.")

    def confirm_many(self, ids: list[int]) -> None:
        raise RuntimeError("Utilise une action : archive.")

    def reject(self, item_id: int) -> None:
        self.reject_many([item_id])

    def reject_many(self, ids: list[int]) -> None:
        rows = db.get_items(self.settings.db_path, ids)
        for item in rows:
            if item["status"] != "proposed":
                continue
            db.set_status(self.settings.db_path, int(item["id"]), "skipped")
            db.memory_forget(self.settings.db_path, sender_email(item.get("sender") or ""))

    # --- scan -----------------------------------------------------------------------------

    def job_scan(self, ctx: JobContext) -> dict[str, Any]:
        settings = self.settings
        path = settings.db_path
        db.init_db(path)
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
            prev_validity = db.get_setting(path, "scan_uidvalidity")
            try:
                last_uid = int(db.get_setting(path, "scan_last_uid") or 0) if prev_validity == validity else 0
            except ValueError:
                last_uid = 0
            criteria = since_criteria(SCAN_DAYS)
            if last_uid:
                criteria = f"UID {last_uid + 1}:* {criteria}"
            found = [uid for uid in self.session.search_uids(criteria) if uid.isdigit() and int(uid) > last_uid]
            uids_asc = sorted(found, key=int)
            known = db.items_by_uids(path, validity, uids_asc)
            extra = db.get_setting(path, "extra_prompt")
            thread_rows = db.list_items(path, statuses=["proposed", "moved"], limit=120)
            created = retried = 0
            chunks = _chunks(uids_asc, SCAN_CHUNK)
            for index, chunk in enumerate(chunks):
                ctx.progress(f"Relevé {index + 1}/{len(chunks)}")
                pending_uids = [
                    uid
                    for uid in chunk
                    if uid not in known or (known[uid].get("status") == "proposed" and known[uid].get("error"))
                ]
                if pending_uids:
                    mails = self.session.fetch_mails(pending_uids, body_chars=settings.inboxzero_body_chars, validity=validity)
                    pending = []
                    for mail in mails:
                        mail.excerpt = sanitize_text(mail.excerpt)
                        pending.append((mail, known.get(mail.uid)))
                    c, r = self._store_classified(pending, extra, validity, thread_rows)
                    created += c
                    retried += r
                db.set_setting(path, "scan_last_uid", str(max(int(uid) for uid in chunk)))
                db.set_setting(path, "scan_uidvalidity", validity)
                if index + 1 < len(chunks) and ctx.should_yield(P_USER, budget_s=SCAN_BUDGET_S):
                    self.bus.publish("queue", {"reason": "scan"})
                    return {"ok": True, "continue": True, "created": created, "retried": retried, "seen": len(uids_asc)}
            self.imap_ok = True
            self.last_scan_count = created
            self.last_scan_at = db.utcnow()
            self.last_error = None
            if created or retried:
                self.bus.publish("queue", {"reason": "scan", "created": created})
            self._prebrief()
            stats = dict(self.last_scan_stats)
            self.bus.publish("scan", {"status": "done", "created": created, "seen": len(uids_asc), "manual": manual})
            return {
                "ok": True,
                "created": created,
                "retried": retried,
                "auto_moved": 0,
                "seen": len(uids_asc),
                "haiku_calls": stats.get("haiku_calls", 0),
                "sonnet_calls": stats.get("sonnet_calls", 0),
            }
        except Exception as exc:
            self.imap_ok = False
            self.last_error = str(exc)[:300]
            self.bus.publish("scan", {"status": "failed", "error": self.last_error})
            raise

    def _store_classified(
        self,
        pending: list[tuple[FetchedMail, dict[str, Any] | None]],
        extra: str,
        validity: str,
        thread_rows: list[dict[str, Any]],
    ) -> tuple[int, int]:
        created = retried = 0
        classified = self._classify_pending(pending, extra, thread_rows)
        for mail, existing, result in classified:
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
                db.update_item(self.settings.db_path, int(existing["id"]), payload)
                db.set_flags(self.settings.db_path, [int(existing["id"])], seen=mail.seen, flagged=mail.flagged)
                retried += 1
            else:
                item_id = db.insert_item(self.settings.db_path, payload)
                thread_rows.append({**payload, "id": item_id})
                created += 1
            if error:
                self.last_error = str(error)
        return created, retried

    def _classify_pending(
        self,
        pending: list[tuple[FetchedMail, dict[str, Any] | None]],
        extra: str,
        thread_rows: list[dict[str, Any]] | None = None,
    ) -> list[tuple[FetchedMail, dict[str, Any] | None, dict[str, Any]]]:
        settings = self.settings
        cheap: list[tuple[FetchedMail, dict[str, Any] | None, dict[str, Any]]] = []
        need_llm: list[tuple[FetchedMail, dict[str, Any] | None]] = []
        for mail, existing in pending:
            result = heuristic_gate(mail.sender, mail.subject, mail.excerpt, mail.headers)
            if result:
                cheap.append((mail, existing, result))
                continue
            remembered = db.memory_lookup(settings.db_path, sender_email(mail.sender))
            if remembered:
                if not remembered.get("summary"):
                    remembered["summary"] = thread_summary(mail.excerpt, remembered.get("reason") or "")
                cheap.append((mail, existing, remembered))
                continue
            inherited = db.category_for_thread(settings.db_path, mail.subject, skip_uid=mail.uid, rows=thread_rows)
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
                packed = [
                    {"sender": mail.sender, "subject": mail.subject, "excerpt": clip(mail.excerpt, HAIKU_EXCERPT)}
                    for mail, _existing in chunk
                ]
                parsed, usage = classify_batch_haiku(
                    api_key=settings.anthropic_api_key,
                    model=settings.anthropic_fast_model,
                    mails=packed,
                    extra_prompt=extra,
                    workspace_id=settings.anthropic_workspace_id,
                )
                self._add_usage(usage, "haiku")
                for (mail, existing), result in zip(chunk, parsed):
                    if should_escalate(result, mail.subject, mail.excerpt):
                        escalate.append((mail, existing))
                    else:
                        haiku_done.append((mail, existing, result))
            except Exception as exc:
                log.warning("haiku batch failed: %s", exc)
                for mail, existing in chunk:
                    guess = heuristic_classification(mail.sender, mail.subject, mail.excerpt, mail.headers)
                    if should_escalate(guess, mail.subject, mail.excerpt):
                        escalate.append((mail, existing))
                    else:
                        haiku_done.append((mail, existing, guess))

        out = list(cheap) + haiku_done
        for mail, existing in escalate:
            try:
                result = classify_with_anthropic(
                    api_key=settings.anthropic_api_key,
                    model=settings.anthropic_model,
                    sender=mail.sender,
                    subject=mail.subject,
                    excerpt=mail.excerpt,
                    extra_prompt=extra,
                    workspace_id=settings.anthropic_workspace_id,
                )
                self._add_usage(result.pop("_usage", None), "sonnet")
            except Exception as exc:
                result = {
                    "category": "read",
                    "reason": "classification impossible",
                    "summary": thread_summary(mail.excerpt),
                    "confidence": 0,
                    "error": str(exc)[:400],
                    "via": "error",
                }
            out.append((mail, existing, result))
        return out

    def _prebrief(self) -> None:
        if not self.settings.llm_ready:
            return
        rows = db.list_items(self.settings.db_path, status="proposed", limit=40)
        picked = 0
        for row in rows:
            if row.get("category") not in {"todo", "waiting"}:
                continue
            if not item_needs_brief(row):
                continue
            self.runner.submit("brief", {"item_id": int(row["id"]), "auto": True}, P_SCAN, dedupe=True)
            picked += 1
            if picked >= PREBRIEF:
                break

    # --- index -----------------------------------------------------------------------------

    def job_index(self, ctx: JobContext) -> dict[str, Any]:
        settings = self.settings
        path = settings.db_path
        db.init_db(path)
        db.seed_mail_index_from_items(path)
        stats = {
            "indexed": db.mail_index_count(path),
            "inbox": int(db.get_setting(path, "mail_index_inbox") or 0),
            "added": 0,
        }
        self.last_index = stats
        if self.session is None:
            return stats
        try:
            validity = self.session.uidvalidity()
            prev = db.get_setting(path, "mail_index_uidvalidity")
            if prev and prev != validity:
                db.clear_mail_index(path)
                db.seed_mail_index_from_items(path)
            db.set_setting(path, "mail_index_uidvalidity", validity)
            status = self.session.status("INBOX")
            total = int(status.get("MESSAGES") or 0)
            db.set_setting(path, "mail_index_inbox", str(total))
            all_uids = self.session.search_uids("ALL")
            gone = db.mark_gone(path, validity, set(all_uids))
            if gone:
                self.bus.publish("queue", {"reason": "gone", "ids": gone})
            have = db.indexed_uids(path, validity, "INBOX")
            missing = [uid for uid in all_uids if uid not in have]
            added = 0
            chunks = _chunks(missing, INDEX_CHUNK)
            remaining = False
            for index, chunk in enumerate(chunks):
                mails = self.session.fetch_mails(chunk, body_chars=settings.inboxzero_body_chars, validity=validity)
                known = db.items_by_uids(path, validity, [mail.uid for mail in mails])
                rows = []
                for mail in mails:
                    hit = known.get(mail.uid)
                    rows.append(
                        {
                            "imap_uid": mail.uid,
                            "uidvalidity": validity,
                            "message_id": mail.message_id,
                            "sender": mail.sender,
                            "subject": mail.subject,
                            "excerpt": sanitize_text(mail.excerpt),
                            "summary": (hit or {}).get("summary") or "",
                            "mailed_at": mail.mailed_at,
                            "attachments": dump_list(mail.attachments),
                            "id": hit["id"] if hit else None,
                        }
                    )
                db.upsert_mail_index_many(path, rows)
                added += len(rows)
                indexed_now = db.mail_index_count(path)
                self.last_index = {"indexed": indexed_now, "inbox": total, "added": added}
                if index + 1 < len(chunks) and ctx.should_yield(P_USER, budget_s=INDEX_BUDGET_S):
                    remaining = True
                    break
                if index % 3 == 2:
                    self.bus.publish("index", {**self.last_index, "running": True})
            stats = {"indexed": db.mail_index_count(path), "inbox": total, "added": added}
            self.last_index = stats
            self.last_index_error = None
            self.bus.publish("index", {**stats, "running": remaining})
            return {**stats, "continue": remaining}
        except Exception as exc:
            self.last_index_error = str(exc)[:300]
            raise

    # --- flags -----------------------------------------------------------------------------

    def job_flags(self, ctx: JobContext) -> dict[str, Any]:
        del ctx
        if self.session is None:
            return {"changed": 0}
        path = self.settings.db_path
        validity = self.session.uidvalidity()
        by_uid = db.inbox_uids_for_status(path, validity)
        if not by_uid:
            return {"changed": 0}
        flags = self.session.fetch_flags(list(by_uid))
        changed = db.sync_flags(path, validity, flags)
        missing = [by_uid[uid] for uid in by_uid if uid not in flags]
        if changed or missing:
            self.bus.publish("queue", {"reason": "flags", "changed": changed})
        self.imap_ok = True
        return {"changed": changed, "checked": len(by_uid)}

    # --- brief -------------------------------------------------------------------------------

    def job_brief(self, ctx: JobContext) -> dict[str, Any]:
        item_id = int(ctx.payload.get("item_id") or 0)
        settings = self.settings
        if not settings.llm_ready:
            raise RuntimeError("Claude non configuré")
        item = db.get_item(settings.db_path, item_id)
        if not item:
            raise KeyError("proposition introuvable")
        if ctx.payload.get("auto") and not item_needs_brief(item):
            return {"skipped": True}
        ctx.progress("Récap en cours")
        extra = db.get_setting(settings.db_path, "extra_prompt")
        rows = db.list_items(settings.db_path, statuses=["proposed", "moved", "skipped"], limit=80)
        if not any(int(row["id"]) == item_id for row in rows):
            rows.append(item)
        thread = next((group for group in cluster_groups(rows) if any(int(row["id"]) == item_id for row in group)), [item])
        thread.sort(key=lambda row: (row.get("mailed_at") or row.get("created_at") or "", row["id"]))
        messages = [
            brief_payload(
                row.get("sender") or "",
                row.get("subject") or "",
                sanitize_text(row.get("excerpt") or ""),
                row.get("attachments"),
            )
            for row in thread[-8:]
        ]
        out, usage = brief_with_anthropic(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            messages=messages,
            subject=item.get("subject") or "",
            extra_prompt=extra,
            workspace_id=settings.anthropic_workspace_id,
            effort=settings.anthropic_effort,
        )
        self._add_usage(usage, "sonnet")
        summary = sanitize_text((out.get("summary") or "").strip() or thread_summary(item.get("excerpt") or "", item.get("reason") or ""))
        db.set_summary(settings.db_path, item_id, summary)
        draft = sanitize_text(out.get("draft") or "")
        signature = db.get_setting(settings.db_path, "signature")
        source = source_blob({**item, "summary": summary, "excerpt": sanitize_text(item.get("excerpt") or "")})
        if check_draft(draft, source, signature):
            db.set_draft(settings.db_path, item_id, "")
            db.set_status(settings.db_path, item_id, item.get("status") or "proposed", error="brouillon écarté : donnée absente")
        else:
            db.set_draft(settings.db_path, item_id, draft)
            if (item.get("error") or "").startswith("brouillon écarté"):
                db.set_status(settings.db_path, item_id, item.get("status") or "proposed", error=None)
        with db.transaction(settings.db_path):
            for row in thread:
                if int(row["id"]) != item_id and (row.get("status") or "") == "proposed":
                    db.set_draft(settings.db_path, int(row["id"]), "")
        updated = db.get_item(settings.db_path, item_id)
        if not updated:
            raise KeyError("proposition introuvable")
        packed = serialize_item(updated, signature)
        self.bus.publish("item", {"item": packed, "reason": "brief"})
        return {"item": packed}

    # --- pièces jointes ------------------------------------------------------------------------

    def job_attachment(self, ctx: JobContext) -> dict[str, Any]:
        item_id = int(ctx.payload.get("item_id") or 0)
        n = int(ctx.payload.get("n") or 0)
        item = db.get_item(self.settings.db_path, item_id)
        if not item:
            raise ValueError("mail introuvable")
        path, mime, filename = self.fetch_attachment(item, n)
        return {"path": str(path), "mime": mime, "filename": filename}

    def fetch_attachment(self, item: dict[str, Any], n: int) -> tuple[Path, str, str]:
        settings = self.settings
        rows = parse_list(item.get("attachments"))
        row = next((entry for entry in rows if int(entry.get("n", -1)) == n), None)
        if row is None:
            raise ValueError("pièce introuvable")
        kind = str(row.get("kind") or "other")
        if kind not in {"pdf", "image", "audio"}:
            raise ValueError("type de pièce non pris en charge")
        filename = str(row.get("filename") or f"piece-{n}")
        path = cache_path(settings.data_dir, int(item["id"]), n, filename)
        if path.is_file() and str(row.get("status") or "") == "ok":
            mime = str(row.get("mime") or row.get("content_type") or "application/octet-stream")
            return path, mime, filename
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
            db.set_attachments(
                settings.db_path,
                int(item["id"]),
                dump_list(upsert_row(rows, n, status="refus", error=result.get("error") or "refusé", text="")),
            )
            self._publish_item(int(item["id"]), "attachment")
            raise ValueError(result.get("error") or "pièce refusée")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        prune_cache(settings.data_dir)
        text = str(result.get("text") or "")
        db.set_attachments(
            settings.db_path,
            int(item["id"]),
            dump_list(
                upsert_row(
                    rows,
                    n,
                    status="ok",
                    error="",
                    text=text,
                    note=result.get("note") or "",
                    mime=result.get("mime") or "",
                )
            ),
        )
        if text:
            stored = db.get_item(settings.db_path, int(item["id"])) or item
            db.set_summary(settings.db_path, int(item["id"]), append_excerpt(stored.get("summary") or "", text))
        self._publish_item(int(item["id"]), "attachment")
        return path, str(result.get("mime") or "application/octet-stream"), filename

    def _publish_item(self, item_id: int, reason: str) -> None:
        stored = db.get_item(self.settings.db_path, item_id)
        if stored:
            signature = db.get_setting(self.settings.db_path, "signature")
            self.bus.publish("item", {"item": serialize_item(stored, signature), "reason": reason})

    # --- Notion -----------------------------------------------------------------------------------

    def job_notion(self, ctx: JobContext) -> dict[str, Any]:
        settings = self.settings
        item_id = int(ctx.payload.get("item_id") or 0)
        note = str(ctx.payload.get("note") or "")
        item = db.get_item(settings.db_path, item_id)
        if not item:
            raise ValueError("mail introuvable")
        token = settings.notion_secret(db.get_setting(settings.db_path, "notion_token"))
        if not token:
            raise ValueError("Jeton Notion manquant (Réglages).")
        uploads = self._notion_uploads(ctx, token, item)
        ctx.progress("Création de la tâche")
        item = db.get_item(settings.db_path, item_id) or item
        created = notion.add_item_to_notion(
            item=item,
            note=note,
            token=token,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            extra_prompt=db.get_setting(settings.db_path, "extra_prompt"),
            workspace_id=settings.anthropic_workspace_id,
            effort=settings.anthropic_effort,
            database_id=settings.notion_database_id,
            project_id=settings.notion_project_id,
            data_source_id=settings.notion_data_source_id,
            uploads=uploads,
        )
        db.set_notion_url(settings.db_path, item_id, created["url"])
        stored = db.get_item(settings.db_path, item_id)
        signature = db.get_setting(settings.db_path, "signature")
        packed = serialize_item(stored, signature) if stored else None
        if packed:
            self.bus.publish("item", {"item": packed, "reason": "notion"})
        return {"url": created["url"], "title": created.get("title") or "", "item": packed}

    def _thread_rows(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        rows = db.list_items(self.settings.db_path, statuses=["proposed", "moved", "skipped"], limit=80)
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
                kind = str(att.get("kind") or "")
                if kind not in {"pdf", "image", "audio"}:
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
            kind = str(att.get("kind") or "")
            filename = str(att.get("filename") or f"piece-{n}")
            ctx.progress(f"Ajout des pièces {index + 1}/{len(todo)}")
            packed = {"kind": kind, "filename": filename, "file_upload_id": ""}
            try:
                fetched = self.runner.call("attachment", {"item_id": int(row["id"]), "n": n}, P_USER, timeout=120)
                packed["filename"] = fetched.get("filename") or filename
                packed["file_upload_id"] = notion.upload_file(token, Path(fetched["path"]), packed["filename"], fetched.get("mime") or "")
            except Exception as exc:
                log.warning("Notion pièce %s : %s", filename, exc)
            out.append(packed)
        return out

    # --- actions ----------------------------------------------------------------------------------

    def snapshot_before(self, items: list[dict[str, Any]]) -> dict[str, Any]:
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

    def apply_local(self, kind: str, items: list[dict[str, Any]], after: dict[str, Any] | None = None) -> None:
        """Server-side optimistic update, before IMAP confirms."""
        ids = [int(item["id"]) for item in items]
        path = self.settings.db_path
        if kind in {"seen", "unseen"}:
            db.set_flags(path, ids, seen=(kind == "seen"))
        elif kind in {"flag", "unflag"}:
            db.set_flags(path, ids, flagged=(kind == "flag"))
        elif kind == "archive":
            db.set_status_many(path, ids, "moved")
        elif kind == "later":
            db.set_status_many(path, ids, "skipped")
        elif kind in {"unlater", "restore"}:
            db.set_status_many(path, ids, "proposed")
        del after

    def revert_local(self, before: dict[str, Any]) -> None:
        path = self.settings.db_path
        for key, snap in before.items():
            try:
                item_id = int(key)
            except ValueError:
                continue
            db.set_flags(path, [item_id], seen=bool(snap.get("seen", True)), flagged=bool(snap.get("flagged", False)))
            db.set_status(path, item_id, str(snap.get("status") or "proposed"))

    def job_action(self, ctx: JobContext) -> dict[str, Any]:
        action_id = int(ctx.payload.get("action_id") or 0)
        path = self.settings.db_path
        action = db.action_get(path, action_id)
        if not action:
            raise ValueError("action introuvable")
        kind = str(action.get("kind") or "")
        before: dict[str, Any] = action.get("before") or {}
        after: dict[str, Any] = action.get("after") or {}
        ids = [int(i) for i in action.get("item_ids") or []]
        if self.session is None and kind in IMAP_KINDS:
            db.action_update(path, action_id, status="failed", error="IMAP non configuré")
            self.revert_local(before)
            raise RuntimeError("IMAP non configuré")
        try:
            touched = self._apply_imap(kind, ids, before, after, ctx)
            db.action_update(path, action_id, status="done", after={**after, **touched})
        except Exception as exc:
            db.action_update(path, action_id, status="failed", error=str(exc)[:400])
            self.revert_local(before)
            self.bus.publish("action", {"action": serialize_action(db.action_get(path, action_id) or action), "status": "failed"})
            for item_id in ids:
                self._publish_item(item_id, "action-failed")
            self.bus.publish("queue", {"reason": "action-failed"})
            raise
        fresh = db.action_get(path, action_id) or action
        self.bus.publish("action", {"action": serialize_action(fresh), "status": "done"})
        for item_id in ids:
            self._publish_item(item_id, "action")
        self.bus.publish("queue", {"reason": "action", "kind": kind})
        return {"action": serialize_action(fresh)}

    def _apply_imap(
        self,
        kind: str,
        ids: list[int],
        before: dict[str, Any],
        after: dict[str, Any],
        ctx: JobContext,
    ) -> dict[str, Any]:
        assert self.session is not None
        del before
        path = self.settings.db_path
        touched: dict[str, Any] = {}
        current = {int(row["id"]): row for row in db.get_items(path, ids)}

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
                db.set_flags(path, ids, seen=value)
            else:
                db.set_flags(path, ids, flagged=value)
            return touched
        if kind == "archive":
            children = self.session.ensure_folders()
            parent, _ = self.session.folder_names()
            per_folder: dict[str, list[int]] = {}
            for item_id in ids:
                row = current.get(item_id) or {}
                if str(row.get("folder") or "INBOX") != "INBOX":
                    continue
                target = str(after.get("folder") or "") or children.get(str(row.get("category") or ""), children.get("read", parent))
                per_folder.setdefault(target, []).append(item_id)
            moved: dict[str, str] = {}
            for folder, group in per_folder.items():
                ctx.progress(f"Archivage vers {folder.split(self.session.delim)[-1]}")
                uids = [str((current.get(i) or {}).get("imap_uid") or "") for i in group]
                uids = [uid for uid in uids if uid]
                mapping = self.session.move(uids, folder, source="INBOX")
                for item_id in group:
                    row = current.get(item_id) or {}
                    uid = str(row.get("imap_uid") or "")
                    new_uid = mapping.get(uid) or self.session.find_uid_by_message_id(str(row.get("message_id") or ""), folder)
                    db.set_folder(path, item_id, folder, new_uid, status="moved")
                    db.memory_remember(path, str(row.get("sender") or ""), str(row.get("category") or ""))
                    moved[str(item_id)] = f"{folder}:{new_uid}"
            touched["moved"] = moved
            touched["folders"] = sorted(per_folder)
            return touched
        if kind == "restore":
            for item_id in ids:
                row = current.get(item_id) or {}
                folder, folder_uid = _location(item_id)
                if folder == "INBOX":
                    db.restore_inbox(path, item_id, str(row.get("imap_uid") or ""))
                    continue
                if not folder_uid:
                    folder_uid = self.session.find_uid_by_message_id(str(row.get("message_id") or ""), folder)
                if not folder_uid:
                    raise RuntimeError("Mail introuvable dans le dossier d’archive.")
                mapping = self.session.move([folder_uid], "INBOX", source=folder)
                new_uid = mapping.get(folder_uid) or self.session.find_uid_by_message_id(str(row.get("message_id") or ""), "INBOX")
                db.restore_inbox(path, item_id, new_uid or str(row.get("imap_uid") or ""))
            return touched
        if kind in {"later", "unlater"}:
            return touched
        raise ValueError(f"action inconnue : {kind}")


def run_in_background(settings: Settings, on_ready: Callable[[Worker], None] | None = None) -> Worker:
    worker = Worker(settings)
    if on_ready:
        on_ready(worker)
    worker.start()
    return worker
