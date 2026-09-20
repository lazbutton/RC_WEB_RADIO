from sqlalchemy import text

# Beets `path:10-rotation` is cwd-relative. The bank is always MEDIA_ROOT=/media.


async def upgrade(conn):
    await conn.execute(
        text(
            """UPDATE music_categories
            SET query = 'path:/media/10-rotation'
            WHERE name = 'Rotation' AND query = 'grouping:rotation'"""
        )
    )


async def downgrade(conn):
    await conn.execute(
        text(
            """UPDATE music_categories
            SET query = 'grouping:rotation'
            WHERE name = 'Rotation' AND query = 'path:/media/10-rotation'"""
        )
    )
