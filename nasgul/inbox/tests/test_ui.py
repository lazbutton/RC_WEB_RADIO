from datetime import datetime, timedelta, timezone

from inboxzero.ui import confidence_pct, date_iso, parse_sender, relative_time, sender_email, sender_name


def test_parse_sender_name_and_email():
    assert parse_sender('Viviane Berreur <viviane@orleans.radiocampus.org>') == (
        "Viviane Berreur",
        "viviane@orleans.radiocampus.org",
    )
    assert sender_name("foo@bar.test") == "foo"
    assert sender_email("foo@bar.test") == "foo@bar.test"
    assert sender_name("") == "—"
    assert sender_email('Name <a@b.c>') == "a@b.c"


def test_relative_time_french():
    now = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    assert relative_time("2026-09-18T13:59:00+00:00", now) == "18/09/2026 15:59 (15:59)"
    assert relative_time("2026-09-18T13:00:00+00:00", now) == "18/09/2026 15:00 (15:00)"
    assert relative_time("2026-09-17T14:00:00+00:00", now) == "17/09/2026 16:00 (hier)"
    assert relative_time("2026-09-16T14:00:00+00:00", now) == "16/09/2026 16:00 (il y a 2 j)"
    assert relative_time("2026-09-10T10:00:00+00:00", now) == "10/09/2026 12:00 (10/09)"
    assert relative_time(None) == ""


def test_date_iso_utc():
    dt = datetime(2026, 9, 8, 14, 56, 50, tzinfo=timezone(timedelta(hours=2)))
    assert date_iso(dt) == "2026-09-08T12:56:50+00:00"


def test_confidence_pct_clamps():
    assert confidence_pct(0.42) == 42
    assert confidence_pct(2) == 100
    assert confidence_pct(None) == 0
