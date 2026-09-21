from radiotomate.scheduler import live as sched_live
from radiotomate.scheduler.metrics import runtime_metrics


def _md(**over) -> dict:
    base = {
        "source": "carts",
        "artist": "A",
        "title": "T",
        "remaining": "120.0",
        "time": "2026-09-21T16:00:12+02:00",
        "next_jingle": {"rid": 4},
        "next_autodj": {"rid": -1},
        "next_cart": {"rid": 9},
        "jingles_queued": 1,
        "autodj_queued": 0,
        "carts_queued": 2,
    }
    base.update(over)
    return base


def test_gate_skips_identical_mid_track_heartbeats():
    sched_live.reset_tick_gate()
    before = runtime_metrics.tick_gated_total
    assert sched_live.should_run_tick(_md(), now=100.0) is True
    assert sched_live.should_run_tick(_md(), now=101.0) is False
    assert sched_live.should_run_tick(_md(), now=102.0) is False
    assert runtime_metrics.tick_gated_total == before + 2


def test_gate_reopens_on_change_minute_or_idle():
    sched_live.reset_tick_gate()
    assert sched_live.should_run_tick(_md(), now=100.0) is True
    # queue depth changed (a push landed)
    assert sched_live.should_run_tick(_md(carts_queued=1), now=101.0) is True
    # minute boundary → anchors may be due
    assert (
        sched_live.should_run_tick(
            _md(carts_queued=1, time="2026-09-21T16:01:00+02:00"), now=102.0
        )
        is True
    )
    # nothing changed but the safety interval elapsed
    assert (
        sched_live.should_run_tick(
            _md(carts_queued=1, time="2026-09-21T16:01:00+02:00"), now=103.0
        )
        is False
    )
    assert (
        sched_live.should_run_tick(
            _md(carts_queued=1, time="2026-09-21T16:01:00+02:00"),
            now=102.0 + sched_live.TICK_MAX_IDLE_SECONDS,
        )
        is True
    )


def test_gate_always_runs_near_track_end():
    sched_live.reset_tick_gate()
    hot = _md(remaining="7.5")
    assert sched_live.should_run_tick(hot, now=100.0) is True
    assert sched_live.should_run_tick(hot, now=101.0) is True
    assert sched_live.should_run_tick(_md(remaining="0"), now=102.0) is True
    assert sched_live.should_run_tick(_md(remaining="garbage"), now=103.0) is True
