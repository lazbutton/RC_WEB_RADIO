from sqlalchemy import text


async def upgrade(conn):
    #### Create the initial schema
    q = text("""CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
                )
                """)
    await conn.execute(q)

    q = text("""CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                permissions JSON NOT NULL DEFAULT '{}'
                )
                """)
    await conn.execute(q)

    q = text("""CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                user_agent TEXT,
                active BOOLEAN DEFAULT TRUE,
                latest_address TEXT,
                created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                latest_action TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """)
    await conn.execute(q)
    q = text("""CREATE INDEX sessions_user_id_idx ON sessions(user_id)""")
    await conn.execute(q)

    q = text("""CREATE TABLE carts (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL UNIQUE,
                path TEXT,
                average_duration INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'playlist',
                schedule_mode TEXT NOT NULL DEFAULT 'timed',
                schedule_correct BOOLEAN NOT NULL DEFAULT FALSE,
                schedule_year TEXT NOT NULL DEFAULT 'timed',
                schedule_month TEXT NOT NULL DEFAULT 'timed',
                schedule_day TEXT NOT NULL DEFAULT 'timed',
                schedule_week TEXT NOT NULL DEFAULT 'timed',
                schedule_day_of_week TEXT NOT NULL DEFAULT 'timed',
                schedule_hour TEXT NOT NULL DEFAULT 'timed',
                schedule_minute TEXT NOT NULL DEFAULT 'timed',
                schedule_second TEXT NOT NULL DEFAULT 'timed',
                created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """)
    await conn.execute(q)

    q = text("""CREATE TABLE sounds (
                id INTEGER PRIMARY KEY,
                cart_id INTEGER NOT NULL,
                rank INTEGER NOT NULL DEFAULT 1,
                path TEXT NOT NULL,
                duration INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                active BOOLEAN DEFAULT TRUE,
                created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_played TIMESTAMP,
                FOREIGN KEY(cart_id) REFERENCES carts(id) ON DELETE CASCADE
                )
                """)
    await conn.execute(q)
    q = text("""CREATE INDEX sounds_cart_id_idx ON sounds(cart_id)""")
    await conn.execute(q)

    q = text("""CREATE TABLE metadata_log (
                id INTEGER PRIMARY KEY,
                on_air TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                source_url TEXT,
                cart_id INTEGER,
                artist TEXT,
                title TEXT,
                album TEXT,
                extra JSON NOT NULL DEFAULT '{}',
                FOREIGN KEY(cart_id) REFERENCES carts(id) ON DELETE SET NULL
                )
                """)
    await conn.execute(q)
    q = text("""CREATE INDEX metadata_log_on_air_idx ON metadata_log(on_air)""")
    await conn.execute(q)

    #### Create basic settings
    q = text("select value from settings where key=:key")
    result = await conn.execute(q, {"key": "SCHEMA_VERSION"})
    if (
        result.scalars().first() is None
    ):  # the opposite may happen if the install was downgraded to v0...
        # which is weird, but happens when developing
        q = text("""INSERT INTO settings (key, value) VALUES (:key, :value)""")
        await conn.execute(q, {"key": "SCHEMA_VERSION", "value": "1"})

    default_settings = {
        "INTERFACE_NAME": "New Trad Radio",
    }
    q = text("""INSERT INTO settings (key, value) VALUES (:key, :value)""")
    for key, value in default_settings.items():
        await conn.execute(q, {"value": value, "key": key})


async def downgrade(conn):
    q = text("DROP TABLE sessions")
    await conn.execute(q)

    q = text("DROP TABLE users")
    await conn.execute(q)

    q = text("DROP TABLE sounds")
    await conn.execute(q)

    q = text("DROP TABLE carts")
    await conn.execute(q)

    # don't drop settings because the update system will attempt to update it
    # we can truncate it, instead
    q = text("""DELETE FROM settings""")
    await conn.execute(q)
    q = text("""INSERT INTO settings (key, value) VALUES (:key, :value)""")
    await conn.execute(q, {"value": "0", "key": "SCHEMA_VERSION"})
