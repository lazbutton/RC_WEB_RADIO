from __future__ import annotations

import threading

import pytest

from regie import migrate as migrate_mod
from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.jobs import P_ACTION, P_INDEX, P_USER
from regie.kernel.registry import EntityKind


def test_migrations_are_idempotent_and_checksummed(dsn):
    assert migrate_mod.migrate(dsn) == []
    status = migrate_mod.status(dsn)
    assert 1 in status["applied"]
    assert status["pending"] == []


def test_auth_users_sessions_permissions(kernel):
    user = kernel.auth.create_user("Lou@Test", "Lou", "un-mot-de-passe-long", "membre")
    assert user["email"] == "lou@test"
    with pytest.raises(ValueError):
        kernel.auth.create_user("x@test", "X", "court", "membre")
    assert kernel.auth.login("lou@test", "mauvais", ip="1.1.1.1") is None
    token, logged = kernel.auth.login("lou@test", "un-mot-de-passe-long", ip="1.1.1.1")
    assert logged["id"] == user["id"]
    resolved = kernel.auth.resolve(token)
    assert resolved and resolved["email"] == "lou@test"
    assert kernel.auth.allows(resolved, "mail", "write")
    assert not kernel.auth.allows(resolved, "settings", "write")
    kernel.auth.set_permission("membre", "settings", "write")
    assert kernel.auth.allows(resolved, "settings", "write")
    kernel.auth.update_user(user["id"], disabled=True)
    assert kernel.auth.resolve(token) is None
    for _ in range(8):
        kernel.auth.login("lou@test", "mauvais", ip="9.9.9.9")
    with pytest.raises(PermissionError):
        kernel.auth.login("lou@test", "un-mot-de-passe-long", ip="9.9.9.9")


def test_secrets_are_encrypted_at_rest(kernel):
    kernel.secrets.put("google", "refresh:laz", "ya29.secret")
    assert kernel.secrets.get("google", "refresh:laz") == "ya29.secret"
    with db.connect(kernel.dsn) as conn:
        raw = db.scalar(conn, "SELECT ciphertext FROM secrets WHERE scope = 'google'")
    assert b"ya29" not in bytes(raw)
    assert kernel.secrets.keys("google") == ["refresh:laz"]
    kernel.secrets.delete("google", "refresh:laz")
    assert kernel.secrets.get("google", "refresh:laz") == ""


def test_registry_links_and_summaries(kernel):
    rows = {"1": {"id": "1", "name": "Viviane"}, "2": {"id": "2", "name": "Lou"}}
    kernel.registry.register(EntityKind(kind="person", module="contacts", label="Personne", icon="user", fetch=lambda ids: [rows[i] for i in ids if i in rows], summarize=lambda r: {"title": r["name"], "subtitle": ""}))
    kernel.registry.register(EntityKind(kind="note", module="planning", label="Note"))
    kernel.links.link("note", 10, "person", 1, role="about")
    kernel.links.link("note", 10, "person", 2)
    linked = kernel.links.of("note", 10)
    assert {row["other_id"] for row in linked} == {"1", "2"}
    assert kernel.links.targets("person", 1, "note") == ["10"]
    summaries = kernel.registry.summaries([("person", "1"), ("person", "9"), ("note", "10")])
    assert summaries["person:1"]["title"] == "Viviane"
    assert summaries["person:9"]["missing"] is True
    assert summaries["note:10"]["title"] == "note 10"
    assert kernel.links.relink("person", 2, 1) >= 1
    assert {row["other_id"] for row in kernel.links.of("note", 10)} == {"1"}
    assert kernel.links.purge("note", 10) == 2  # deux rôles distincts vers la même personne


def test_actions_perform_undo_and_failure(kernel):
    state = {"a": "off", "b": "off"}

    def apply(ctx: ActionCtx):
        for i in ctx.ids:
            if i == "boom":
                raise RuntimeError("cassé")
            state[i] = ctx.params.get("value", "on")
        return {"set": ctx.ids}

    def revert(ctx: ActionCtx):
        for i, previous in ctx.before.items():
            state[i] = previous

    kernel.actions.register(ActionSpec(kind="toggle", module="mail", entity_kind="switch", label="Basculer", apply=apply, revert=revert, snapshot=lambda ids: {i: state.get(i, "off") for i in ids}))
    done = kernel.actions.perform("toggle", ["a", "b"], {"value": "on"}, actor_id=None)
    assert done["status"] == "done" and state == {"a": "on", "b": "on"}
    assert done["label"] == "Basculer · 2 éléments"
    undone = kernel.actions.undo(done["id"])
    assert undone["reverted"] is True and state == {"a": "off", "b": "off"}
    assert kernel.actions.get(done["id"])["status"] == "undone"
    with pytest.raises(ValueError):
        kernel.actions.undo(done["id"])
    failed = kernel.actions.perform("toggle", ["a", "boom"], {"value": "on"})
    assert failed["status"] == "failed" and "cassé" in failed["error"]
    assert state["a"] == "off", "le retour arrière remet l'état avant échec"
    history = kernel.actions.recent(10, entity_id="a")
    assert [row["status"] for row in history] == ["failed", "undone"]


def test_async_action_runs_as_p0_job(kernel):
    seen: list[str] = []
    kernel.actions.register(ActionSpec(kind="slow", module="mail", entity_kind="thing", label="Lent", apply=lambda ctx: seen.extend(ctx.ids) or {"ok": True}, lane="imap"))
    pending = kernel.actions.perform("slow", ["x"])
    assert pending["status"] == "pending" and pending["job_id"]
    assert kernel.jobs.has_pending("imap", P_ACTION)
    kernel.jobs.drain()
    assert seen == ["x"]
    assert kernel.actions.get(pending["id"])["status"] == "done"
    assert kernel.jobs.get(pending["job_id"])["status"] == "done"


def test_jobs_priority_lease_retry_and_dead(kernel):
    order: list[str] = []
    attempts = {"n": 0}

    def flaky(ctx):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("encore")
        order.append("flaky")
        return {"tries": attempts["n"]}

    kernel.jobs.register("flaky", flaky, "imap")
    kernel.jobs.register("quick", lambda ctx: order.append("quick") or {}, "imap")
    kernel.jobs.register("always", lambda ctx: (_ for _ in ()).throw(RuntimeError("non")), "imap")
    kernel.jobs.submit("flaky", {}, P_INDEX, max_attempts=5)
    kernel.jobs.submit("quick", {}, P_ACTION)
    dup = kernel.jobs.submit("quick", {}, P_ACTION, dedupe=True)
    assert dup["status"] == "queued"
    kernel.jobs.drain()
    assert order == ["quick", "quick"] or order == ["quick"]
    with db.connect(kernel.dsn) as conn:
        db.execute(conn, "UPDATE jobs SET run_at = now() WHERE kind = 'flaky'")
    kernel.jobs.drain()
    with db.connect(kernel.dsn) as conn:
        db.execute(conn, "UPDATE jobs SET run_at = now() WHERE kind = 'flaky'")
    kernel.jobs.drain()
    flaky_job = kernel.jobs.recent(10, kinds=["flaky"])[0]
    assert flaky_job["status"] == "done" and flaky_job["result"]["tries"] == 3
    dead = kernel.jobs.submit("always", {}, P_USER, max_attempts=1)
    kernel.jobs.drain()
    assert kernel.jobs.get(dead["id"])["status"] == "dead"
    assert kernel.jobs.retry(dead["id"]) is True
    snap = kernel.jobs.snapshot()
    assert "imap" in snap["lanes"]


def test_jobs_call_blocks_until_done_with_worker_thread(kernel):
    kernel.jobs.register("echo", lambda ctx: {"echo": ctx.payload.get("v")}, "llm")
    kernel.jobs._lanes = {"llm": 1}
    kernel.jobs.start()
    try:
        assert kernel.jobs.call("echo", {"v": 7}, timeout=5) == {"echo": 7}
    finally:
        kernel.jobs.stop()


def test_scheduler_and_outbox(kernel):
    fired: list[dict] = []
    kernel.jobs.register("tick", lambda ctx: {"ok": True}, "maintenance")
    kernel.scheduler.ensure("tick", 60, priority=3)
    assert kernel.scheduler.tick() == 1
    assert kernel.scheduler.tick() == 0
    kernel.outbox.on("mail.*", fired.append)
    kernel.outbox.on("podcast.published", fired.append)
    kernel.outbox.emit("mail.archived", {"id": 1})
    kernel.outbox.emit("podcast.published", {"id": 2})
    kernel.outbox.emit("other", {})
    assert kernel.outbox.deliver_pending() == 3
    assert [f["topic"] for f in fired] == ["mail.archived", "podcast.published"]
    assert [e["type"] for e in kernel.bus.since(0)][-3:] == ["mail.archived", "podcast.published", "other"]


def test_search_prefix_accents_and_fallback(kernel):
    kernel.search.index("person", "1", "Viviane Berreur", "Radio Campus", "Programmes formations volontaires civique")
    kernel.search.index("event", "7", "Concert Jasmine Not Jafar", "Astrolabe", "10 octobre")
    hits = kernel.search.query("vivi form")
    assert [h["id"] for h in hits] == ["1"]
    assert kernel.search.query("jasmine")[0]["kind"] == "event"
    assert kernel.search.query("civique", kinds=["event"]) == []
    assert kernel.search.query("Bérreur")[0]["id"] == "1"
    kernel.search.remove("event", "7")
    assert kernel.search.count() == 1


def test_notifications_and_files(kernel, media_root):
    user = kernel.auth.create_user("lou@test", "Lou", "un-mot-de-passe-long")
    ids = kernel.notifications.notify([user["id"]], "task", "Lou t'a assigné une tâche", "task", 5, "/planning?task=5")
    assert len(ids) == 1
    assert kernel.notifications.unread_count(user["id"]) == 1
    assert kernel.notifications.mark_read(user["id"]) == 1
    (media_root / "40-emissions" / "Hph" / "Entière").mkdir(parents=True)
    (media_root / "40-emissions" / "Hph" / "Entière" / "2026-09-11_hph_entiere.mp3").write_bytes(b"ID3" + b"\0" * 100)
    listing = kernel.files.list("40-emissions/Hph/Entière")
    assert listing["files"][0]["kind"] == "audio" and listing["writable"] is True
    assert kernel.files.can_write("20-archives/x") is False
    with pytest.raises(PermissionError):
        kernel.files.write_text("20-archives/notes.txt", "non")
    with pytest.raises(PermissionError):
        kernel.files.resolve("../../etc/passwd")
    written = kernel.files.write_text("40-emissions/Hph/Entière/2026-09-11_hph_bornes.json", "{}")
    assert written["path"].endswith("_bornes.json")
    assert kernel.files.scan("40-emissions") == 2
    assert kernel.files.indexed("40-emissions/Hph/Entière/2026-09-11_hph_entiere.mp3")["kind"] == "audio"


def test_concurrent_claims_do_not_double_run(kernel):
    ran: list[int] = []
    lock = threading.Lock()

    def handler(ctx):
        with lock:
            ran.append(ctx.id)
        return {}

    kernel.jobs.register("par", handler, "media")
    for _ in range(12):
        kernel.jobs.submit("par", {}, P_USER)

    def worker():
        while True:
            row = kernel.jobs.claim("media")
            if not row:
                break
            kernel.jobs._execute(row)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ran) == 12 and len(set(ran)) == 12
