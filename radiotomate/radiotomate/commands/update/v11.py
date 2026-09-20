from sqlalchemy import text


async def upgrade(conn):
    await conn.execute(
        text("UPDATE carts SET title = 'Jingles' WHERE title = 'Jingles NTR'")
    )
    await conn.execute(
        text(
            """UPDATE clocks
            SET fallback_cart_title = 'Jingles'
            WHERE fallback_cart_title = 'Jingles NTR'"""
        )
    )
    await conn.execute(
        text(
            """UPDATE clock_positions
            SET cart_title = 'Jingles'
            WHERE cart_title = 'Jingles NTR'"""
        )
    )
    await conn.execute(
        text(
            """UPDATE clock_positions
            SET fallback_cart_title = 'Jingles'
            WHERE fallback_cart_title = 'Jingles NTR'"""
        )
    )
    await conn.execute(
        text(
            """UPDATE settings
            SET value = 'BUTTON'
            WHERE key = 'INTERFACE_NAME'
              AND value IN ('New Trad Radio', 'NTR')"""
        )
    )
    await conn.execute(
        text(
            """UPDATE sounds
            SET title = REPLACE(title, 'ID NTR', 'ID BUTTON')
            WHERE title LIKE '%ID NTR%'"""
        )
    )


async def downgrade(_conn):
    return
