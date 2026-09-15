from sqlalchemy import text


async def upgrade(conn):
    # Adds Sound.uploader_id
    q = text("ALTER TABLE sounds ADD COLUMN uploader_id INTEGER")
    await conn.execute(q)


async def downgrade(conn):
    q = text("ALTER TABLE sounds DROP COLUMN uploader_id")
    await conn.execute(q)
