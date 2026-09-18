from pathlib import Path

from sqlalchemy import text

from radiotomate.commands.update import do_update
from radiotomate.db import QuartAlchemy

ESSENTIAL_TABLES = {
    "settings",
    "users",
    "carts",
    "clocks",
    "autodj_slots",
    "rundown_items",
    "playout_commands",
}


def _latest_schema_version() -> int:
    from importlib import import_module
    from pkgutil import iter_modules

    updates = import_module("radiotomate.commands.update")
    latest = 0
    for modinfo in iter_modules(updates.__path__):
        if modinfo.name.startswith("v"):
            latest = max(latest, int(modinfo.name[1:]))
    return latest


async def _schema_version(engine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("select value from settings where key=:key"),
            {"key": "SCHEMA_VERSION"},
        )
        return int(result.scalars().first())


async def _table_names(engine) -> set[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("select name from sqlite_master where type='table'"),
        )
        return {row[0] for row in result}


async def test_migrations_blank_database_then_idempotent(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    db = QuartAlchemy()
    db.init_from_config({"url": f"sqlite+aiosqlite:///{db_path}"}, {})
    latest = _latest_schema_version()
    assert latest >= 1

    await do_update(db.engine)
    assert await _schema_version(db.engine) == latest
    names = await _table_names(db.engine)
    missing = ESSENTIAL_TABLES - names
    assert not missing, missing

    await do_update(db.engine)
    assert await _schema_version(db.engine) == latest
    await db.after_serving()
