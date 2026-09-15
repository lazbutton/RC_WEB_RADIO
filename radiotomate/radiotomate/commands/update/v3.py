from sqlalchemy import text


async def upgrade(conn):
    "Adds AutoDJSchedule"
    q = text("""CREATE TABLE autodj_slots (
                id INTEGER PRIMARY KEY,
                day_of_week INTEGER NOT NULL 
                    CHECK (0 <= day_of_week AND day_of_week <= 6),
                minute INTEGER NOT NULL 
                    CHECK (0 <= day_of_week AND day_of_week < 1440),
                color TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                constraints JSON NOT NULL DEFAULT '{}',
                created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
    await conn.execute(q)

    q = text("""CREATE UNIQUE INDEX ix_autodj_slots_uq_day_minute
             ON autodj_slots (day_of_week, minute)""")
    await conn.execute(q)

    q = text("""INSERT INTO autodj_slots (day_of_week, minute, color)
             VALUES (:d, 0, '#efefef')""")
    for d in range(7):
        await conn.execute(q, {"d": d})


async def downgrade(conn):
    q = text("DROP INDEX ix_autodj_slots_uq_day_minute")
    q = text("DROP TABLE autodj_slots")
    await conn.execute(q)
