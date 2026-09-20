from sqlalchemy import text


async def upgrade(conn):
    await conn.execute(text("ALTER TABLE carts ADD COLUMN bank_folder TEXT"))
    await conn.execute(
        text(
            """
            UPDATE carts
            SET bank_folder = '30-habillage/jingles'
            WHERE title = 'Jingles'
            """
        )
    )


async def downgrade(_conn):
    return
