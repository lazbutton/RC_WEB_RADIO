from sqlalchemy import text


async def upgrade(conn):
    # Adds Sound.peak and .gain
    q = text("ALTER TABLE sounds ADD COLUMN gain FLOAT")
    await conn.execute(q)
    q = text("ALTER TABLE sounds ADD COLUMN peak FLOAT")
    await conn.execute(q)


async def downgrade(conn):
    q = text("ALTER TABLE sounds DROP COLUMN peak")
    await conn.execute(q)
    q = text("ALTER TABLE sounds DROP COLUMN gain")
    await conn.execute(q)
