from sqlalchemy import text


async def upgrade(conn):
    # Adds Cart.url
    q = text("ALTER TABLE carts ADD COLUMN url TEXT")
    await conn.execute(q)


async def downgrade(conn):
    q = text("ALTER TABLE carts DROP COLUMN url")
    await conn.execute(q)
