from sqlalchemy import text


async def upgrade(conn):
    # Adds Cart.max_duration
    q = text("ALTER TABLE carts ADD COLUMN max_duration INTEGER")
    await conn.execute(q)


async def downgrade(conn):
    q = text("ALTER TABLE carts DROP COLUMN max_duration")
    await conn.execute(q)
