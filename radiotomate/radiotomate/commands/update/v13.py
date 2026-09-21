"""
One scheduler (ENF-05): carts that still carry the *untouched* default cron
(``* * * * * 0 0`` = every hour at :00, the form default nobody chose) move to the
``clock`` schedule mode and lose their APScheduler job. Carts with a deliberate
cron stay ``timed``.
"""

from sqlalchemy import text

DEFAULT_CRON_WHERE = """
    schedule_mode = 'TIMED'
    AND schedule_year = '*' AND schedule_month = '*' AND schedule_day = '*'
    AND schedule_week = '*' AND schedule_day_of_week = '*'
    AND schedule_hour = '*' AND schedule_minute = '0'
"""


async def upgrade(conn):
    rows = await conn.execute(text(f"SELECT id FROM carts WHERE {DEFAULT_CRON_WHERE}"))
    ids = [str(row[0]) for row in rows.fetchall()]
    if not ids:
        return
    await conn.execute(
        text(
            f"UPDATE carts SET schedule_mode = 'CLOCK', schedule_correct = 1 "
            f"WHERE {DEFAULT_CRON_WHERE}"
        )
    )
    tables = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'apscheduler_schedules'"
        )
    )
    if tables.fetchall():
        placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
        await conn.execute(
            text(f"DELETE FROM apscheduler_schedules WHERE id IN ({placeholders})"),
            {f"id{i}": cart_id for i, cart_id in enumerate(ids)},
        )


async def downgrade(conn):
    await conn.execute(
        text("UPDATE carts SET schedule_mode = 'TIMED' WHERE schedule_mode = 'CLOCK'")
    )
