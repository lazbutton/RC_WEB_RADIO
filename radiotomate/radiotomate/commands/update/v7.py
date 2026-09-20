from sqlalchemy import text

CLOCK_24 = "24/24 Rotation habillée"
CLOCK_JOURNEE = "Journée pubs"
CART_JINGLES = "Jingles"
CART_PUBS = "Pubs"
COLOR_24 = "#3d5a4c"
COLOR_JOURNEE = "#c45c26"


async def upgrade(conn):
    await conn.execute(
        text(
            """CREATE TABLE music_categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                query TEXT NOT NULL,
                empty_query TEXT NOT NULL DEFAULT ''
            )"""
        )
    )
    await conn.execute(
        text(
            """CREATE TABLE clocks (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                fallback_cart_title TEXT NOT NULL DEFAULT ''
            )"""
        )
    )
    await conn.execute(
        text(
            """CREATE TABLE clock_positions (
                id INTEGER PRIMARY KEY,
                clock_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL
                    CHECK (kind IN ('musique', 'jingle', 'son', 'pub')),
                when_mode TEXT NOT NULL
                    CHECK (when_mode IN ('sequential', 'anchored')),
                minute INTEGER
                    CHECK (minute IS NULL OR (minute >= 0 AND minute <= 59)),
                sync TEXT
                    CHECK (sync IS NULL OR sync IN ('dure', 'molle')),
                category_id INTEGER,
                cart_title TEXT,
                fallback_cart_title TEXT,
                FOREIGN KEY(clock_id) REFERENCES clocks(id) ON DELETE CASCADE,
                FOREIGN KEY(category_id) REFERENCES music_categories(id),
                CHECK (
                    (when_mode = 'sequential' AND minute IS NULL) OR
                    (when_mode = 'anchored' AND minute IS NOT NULL)
                )
            )"""
        )
    )
    await conn.execute(
        text(
            """CREATE UNIQUE INDEX ix_clock_positions_anchor
            ON clock_positions (clock_id, minute)
            WHERE when_mode = 'anchored'"""
        )
    )
    await conn.execute(
        text("ALTER TABLE autodj_slots ADD COLUMN clock_id INTEGER")
    )

    result = await conn.execute(
        text(
            """INSERT INTO music_categories (name, query, empty_query)
            VALUES ('Rotation', 'grouping:rotation', 'path:Music')
            RETURNING id"""
        )
    )
    rotation_id = result.scalar_one()

    result = await conn.execute(
        text(
            """INSERT INTO clocks (name, fallback_cart_title)
            VALUES (:name, :fallback)
            RETURNING id"""
        ),
        {"name": CLOCK_24, "fallback": CART_JINGLES},
    )
    clock_24_id = result.scalar_one()

    result = await conn.execute(
        text(
            """INSERT INTO clocks (name, fallback_cart_title)
            VALUES (:name, :fallback)
            RETURNING id"""
        ),
        {"name": CLOCK_JOURNEE, "fallback": CART_JINGLES},
    )
    clock_journee_id = result.scalar_one()

    pos = text(
        """INSERT INTO clock_positions (
            clock_id, sort_order, kind, when_mode, minute, sync,
            category_id, cart_title, fallback_cart_title
        ) VALUES (
            :clock_id, :sort_order, :kind, :when_mode, :minute, :sync,
            :category_id, :cart_title, :fallback_cart_title
        )"""
    )
    for row in (
        {
            "clock_id": clock_24_id,
            "sort_order": 1,
            "kind": "jingle",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": None,
            "cart_title": CART_JINGLES,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_24_id,
            "sort_order": 2,
            "kind": "musique",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": rotation_id,
            "cart_title": None,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_24_id,
            "sort_order": 3,
            "kind": "musique",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": rotation_id,
            "cart_title": None,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_24_id,
            "sort_order": 4,
            "kind": "musique",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": rotation_id,
            "cart_title": None,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_journee_id,
            "sort_order": 0,
            "kind": "pub",
            "when_mode": "anchored",
            "minute": 20,
            "sync": "dure",
            "category_id": None,
            "cart_title": CART_PUBS,
            "fallback_cart_title": CART_JINGLES,
        },
        {
            "clock_id": clock_journee_id,
            "sort_order": 0,
            "kind": "pub",
            "when_mode": "anchored",
            "minute": 40,
            "sync": "dure",
            "category_id": None,
            "cart_title": CART_PUBS,
            "fallback_cart_title": CART_JINGLES,
        },
        {
            "clock_id": clock_journee_id,
            "sort_order": 1,
            "kind": "jingle",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": None,
            "cart_title": CART_JINGLES,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_journee_id,
            "sort_order": 2,
            "kind": "musique",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": rotation_id,
            "cart_title": None,
            "fallback_cart_title": None,
        },
        {
            "clock_id": clock_journee_id,
            "sort_order": 3,
            "kind": "musique",
            "when_mode": "sequential",
            "minute": None,
            "sync": None,
            "category_id": rotation_id,
            "cart_title": None,
            "fallback_cart_title": None,
        },
    ):
        await conn.execute(pos, row)

    await conn.execute(
        text(
            """UPDATE autodj_slots
            SET clock_id = :cid, title = :title, color = :color
            WHERE minute = 0"""
        ),
        {"cid": clock_24_id, "title": CLOCK_24, "color": COLOR_24},
    )
    insert_slot = text(
        """INSERT INTO autodj_slots (
            day_of_week, minute, color, title, constraints, clock_id
        ) VALUES (:d, :minute, :color, :title, '{}', :cid)"""
    )
    for d in range(7):
        await conn.execute(
            insert_slot,
            {
                "d": d,
                "minute": 10 * 60,
                "color": COLOR_JOURNEE,
                "title": CLOCK_JOURNEE,
                "cid": clock_journee_id,
            },
        )
        await conn.execute(
            insert_slot,
            {
                "d": d,
                "minute": 18 * 60,
                "color": COLOR_24,
                "title": CLOCK_24,
                "cid": clock_24_id,
            },
        )


async def downgrade(conn):
    await conn.execute(
        text(
            """DELETE FROM autodj_slots
            WHERE minute IN (600, 1080)"""
        )
    )
    await conn.execute(
        text(
            """UPDATE autodj_slots
            SET clock_id = NULL, title = '', color = '#efefef'
            WHERE minute = 0"""
        )
    )
    await conn.execute(text("ALTER TABLE autodj_slots DROP COLUMN clock_id"))
    await conn.execute(text("DROP INDEX IF EXISTS ix_clock_positions_anchor"))
    await conn.execute(text("DROP TABLE clock_positions"))
    await conn.execute(text("DROP TABLE clocks"))
    await conn.execute(text("DROP TABLE music_categories"))
