from sqlalchemy import text


async def upgrade(conn):
    await conn.execute(
        text(
            """
            CREATE TABLE emissions (
                id INTEGER NOT NULL PRIMARY KEY,
                scope VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                artist VARCHAR NOT NULL DEFAULT '',
                day_of_week INTEGER,
                start_minute INTEGER,
                end_minute INTEGER,
                starts_at DATETIME,
                ends_at DATETIME,
                version INTEGER NOT NULL DEFAULT 1,
                created DATETIME NOT NULL,
                modified DATETIME NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text("CREATE INDEX ix_emissions_scope ON emissions (scope)")
    )


async def downgrade(conn):
    await conn.execute(text("DROP INDEX IF EXISTS ix_emissions_scope"))
    await conn.execute(text("DROP TABLE IF EXISTS emissions"))
