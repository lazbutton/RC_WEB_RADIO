"""
One scheduler: the retired ``timed`` cron mode becomes ``clock`` and the
APScheduler tables (``apscheduler_*``) are dropped. ``max_duration`` skips are
in-process timers now (``radiotomate.scheduler.timers``).
"""

from sqlalchemy import text


async def upgrade(conn):
    await conn.execute(
        text(
            "UPDATE carts SET schedule_mode = 'CLOCK', schedule_correct = 1 "
            "WHERE schedule_mode = 'TIMED'"
        )
    )
    tables = await conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'apscheduler_%'"
        )
    )
    for (name,) in tables.fetchall():
        await conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))


async def downgrade(conn):
    # Tables come back empty on the next APScheduler start (if ever reinstalled);
    # carts stay CLOCK, nothing to undo safely.
    return
