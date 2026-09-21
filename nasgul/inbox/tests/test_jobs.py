from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from inboxzero import db
from inboxzero.imaputil import expand_uid_set, parse_copyuid, parse_flags_lines, since_criteria, sort_uids
from inboxzero.jobs import LANE_IMAP, LANE_LLM, P_ACTION, P_INDEX, P_SCAN, P_USER, EventBus, JobRunner, sse_format


def test_priority_order_and_continuation(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    runner = JobRunner(path, persist=True)
    order: list[str] = []
    rounds = {"n": 0}

    def slow(ctx):
        order.append(f"slow{rounds['n']}")
        rounds["n"] += 1
        return {"continue": rounds["n"] < 2}

    def fast(ctx):
        order.append("fast")
        return {"ok": True}

    runner.register("slow", slow, LANE_IMAP)
    runner.register("fast", fast, LANE_IMAP)
    runner.submit("slow", {}, P_INDEX)
    runner.submit("fast", {}, P_ACTION)
    runner.submit("slow", {}, P_INDEX, dedupe=True)
    assert runner.has_pending(P_ACTION, lane=LANE_IMAP)
    runner.drain()
    assert order[0] == "fast"
    assert order.count("slow0") == 1
    stored = db.jobs_recent(path, 10)
    assert {job["kind"] for job in stored} == {"slow", "fast"}
    assert all(job["status"] == "done" for job in stored)


def test_threaded_lanes_do_not_block_each_other(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    bus = EventBus()
    runner = JobRunner(path, bus=bus, persist=False)
    gate = threading.Event()

    def blocking(ctx):
        gate.wait(2)
        return {"waited": True}

    def quick(ctx):
        ctx.progress("hop")
        return {"quick": True}

    runner.register("blocking", blocking, LANE_LLM)
    runner.register("quick", quick, LANE_IMAP)
    runner.start()
    try:
        slow_job = runner.submit("blocking", {}, P_USER)
        result = runner.call("quick", {}, P_ACTION, timeout=2)
        assert result == {"quick": True}
        assert not slow_job.done.is_set()
        gate.set()
        assert slow_job.done.wait(2)
        assert slow_job.result == {"waited": True}
        kinds = [event["type"] for event in bus.history]
        assert "job" in kinds
        progressed = [event for event in bus.history if event["data"].get("progress") == "hop"]
        assert progressed
    finally:
        runner.stop()


def test_failed_job_is_reported(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    runner = JobRunner(path, persist=True)

    def boom(ctx):
        raise ValueError("cassé")

    runner.register("boom", boom, LANE_IMAP)
    job = runner.submit("boom", {}, P_SCAN)
    runner.drain()
    assert job.status == "failed"
    assert job.error == "cassé"
    assert db.job_get(path, job.id)["status"] == "failed"
    try:
        runner.run_inline("boom", {})
    except RuntimeError as exc:
        assert "cassé" in str(exc)
    else:
        raise AssertionError("run_inline doit lever")


def test_event_bus_replay_and_sse_format():
    bus = EventBus(history=3)
    q = bus.subscribe()
    for n in range(4):
        bus.publish("queue", {"n": n})
    assert [event["data"]["n"] for event in bus.since(0)] == [1, 2, 3]
    assert q.qsize() == 4
    text = sse_format(bus.history[-1])
    assert text.startswith("id: 4\nevent: queue\ndata: {\"n\": 3}")
    bus.unsubscribe(q)
    bus.publish("queue", {"n": 9})
    assert q.qsize() == 4


def test_imap_parsers():
    assert expand_uid_set("5,7:9,12") == ["5", "7", "8", "9", "12"]
    mapping = parse_copyuid([b"[COPYUID 1717 5,7:8 1:3] Move completed."])
    assert mapping == {"5": "1", "7": "2", "8": "3"}
    flags = parse_flags_lines([b"1 (UID 345 FLAGS (\\Seen \\Flagged))", b"2 (FLAGS () UID 346)", None])
    assert flags == {"345": (True, True), "346": (False, False)}
    assert sort_uids(["3", "12", "7"]) == ["12", "7", "3"]
    stamp = since_criteria(30, datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert stamp == "SINCE 22-Aug-2026"
