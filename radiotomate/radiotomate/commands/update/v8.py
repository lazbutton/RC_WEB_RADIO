"""Durable execution schema: stable cart references, rundown and outbox."""

from sqlalchemy import text


async def upgrade(conn):
    await conn.execute(
        text(
            "ALTER TABLE clocks "
            "ADD COLUMN fallback_cart_id INTEGER REFERENCES carts(id)"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE clock_positions "
            "ADD COLUMN cart_id INTEGER REFERENCES carts(id)"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE clock_positions "
            "ADD COLUMN fallback_cart_id INTEGER REFERENCES carts(id)"
        )
    )
    await conn.execute(
        text("ALTER TABLE clocks ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    )
    await conn.execute(
        text("ALTER TABLE autodj_slots ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    )
    await conn.execute(
        text("ALTER TABLE carts ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    )

    await conn.execute(
        text(
            """UPDATE clocks
            SET fallback_cart_id = (
                SELECT carts.id FROM carts
                WHERE carts.title = clocks.fallback_cart_title
            )
            WHERE fallback_cart_title <> ''"""
        )
    )
    await conn.execute(
        text(
            """UPDATE clock_positions
            SET cart_id = (
                SELECT carts.id FROM carts
                WHERE carts.title = clock_positions.cart_title
            )
            WHERE cart_title IS NOT NULL"""
        )
    )
    await conn.execute(
        text(
            """UPDATE clock_positions
            SET fallback_cart_id = (
                SELECT carts.id FROM carts
                WHERE carts.title = clock_positions.fallback_cart_title
            )
            WHERE fallback_cart_title IS NOT NULL"""
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_clocks_fallback_cart_id ON clocks (fallback_cart_id)"
        )
    )
    await conn.execute(
        text("CREATE INDEX ix_clock_positions_cart_id ON clock_positions (cart_id)")
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_clock_positions_fallback_cart_id "
            "ON clock_positions (fallback_cart_id)"
        )
    )

    await conn.execute(
        text(
            """CREATE TABLE programming_versions (
                id TEXT PRIMARY KEY,
                created DATETIME NOT NULL,
                source TEXT NOT NULL DEFAULT 'autodj',
                description TEXT NOT NULL DEFAULT ''
            )"""
        )
    )
    await conn.execute(
        text(
            """CREATE TABLE rundown_items (
                id TEXT PRIMARY KEY,
                programming_version_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                planned_at DATETIME NOT NULL,
                duration FLOAT NOT NULL DEFAULT 0,
                kind TEXT NOT NULL,
                when_mode TEXT NOT NULL,
                sync TEXT,
                queue TEXT NOT NULL,
                resource TEXT NOT NULL,
                path TEXT,
                clock_name TEXT,
                daypart TEXT,
                details JSON NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                reason TEXT,
                origin TEXT NOT NULL DEFAULT 'clock',
                clock_id INTEGER,
                clock_position_id INTEGER,
                cart_id INTEGER,
                sound_id INTEGER,
                beets_id INTEGER,
                fallback_used BOOLEAN NOT NULL DEFAULT 0,
                created DATETIME NOT NULL,
                reserved_at DATETIME,
                sent_at DATETIME,
                accepted_at DATETIME,
                queued_at DATETIME,
                started_at DATETIME,
                closed_at DATETIME,
                version INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(programming_version_id)
                    REFERENCES programming_versions(id),
                FOREIGN KEY(clock_id) REFERENCES clocks(id),
                FOREIGN KEY(clock_position_id) REFERENCES clock_positions(id),
                FOREIGN KEY(cart_id) REFERENCES carts(id),
                FOREIGN KEY(sound_id) REFERENCES sounds(id)
            )"""
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_rundown_items_planned "
            "ON rundown_items (status, planned_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_rundown_items_programming_version "
            "ON rundown_items (programming_version_id, sequence)"
        )
    )

    await conn.execute(
        text(
            """CREATE TABLE playout_commands (
                id TEXT PRIMARY KEY,
                rundown_item_id TEXT,
                action TEXT NOT NULL,
                queue TEXT,
                payload JSON NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at DATETIME NOT NULL,
                lease_until DATETIME,
                last_error TEXT,
                response JSON,
                created DATETIME NOT NULL,
                sent_at DATETIME,
                acknowledged_at DATETIME,
                FOREIGN KEY(rundown_item_id) REFERENCES rundown_items(id)
            )"""
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_playout_commands_pending "
            "ON playout_commands (status, available_at)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_playout_commands_item "
            "ON playout_commands (rundown_item_id)"
        )
    )

    await conn.execute(
        text(
            "ALTER TABLE metadata_log "
            "ADD COLUMN rundown_item_id TEXT REFERENCES rundown_items(id)"
        )
    )
    await conn.execute(
        text(
            "ALTER TABLE metadata_log "
            "ADD COLUMN playout_command_id TEXT REFERENCES playout_commands(id)"
        )
    )
    await conn.execute(
        text(
            "CREATE INDEX ix_metadata_log_rundown_item_id "
            "ON metadata_log (rundown_item_id)"
        )
    )


async def downgrade(conn):
    await conn.execute(text("DROP INDEX IF EXISTS ix_metadata_log_rundown_item_id"))
    await conn.execute(text("ALTER TABLE metadata_log DROP COLUMN playout_command_id"))
    await conn.execute(text("ALTER TABLE metadata_log DROP COLUMN rundown_item_id"))
    await conn.execute(text("DROP TABLE playout_commands"))
    await conn.execute(text("DROP TABLE rundown_items"))
    await conn.execute(text("DROP TABLE programming_versions"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_clock_positions_fallback_cart_id"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_clock_positions_cart_id"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_clocks_fallback_cart_id"))
    await conn.execute(text("ALTER TABLE carts DROP COLUMN version"))
    await conn.execute(text("ALTER TABLE autodj_slots DROP COLUMN version"))
    await conn.execute(text("ALTER TABLE clocks DROP COLUMN version"))
    await conn.execute(text("ALTER TABLE clock_positions DROP COLUMN fallback_cart_id"))
    await conn.execute(text("ALTER TABLE clock_positions DROP COLUMN cart_id"))
    await conn.execute(text("ALTER TABLE clocks DROP COLUMN fallback_cart_id"))
