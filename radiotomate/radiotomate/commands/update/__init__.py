"""
DB Schema upgrade system.

Each version of the schema is in a sub-module, named `vX.py`, where `X` is the version
number. Each sub-module must define functions `upgrade(conn)` and `downgrade(conn)`,
that will be called with a SQLAlchemy connection object.

"""

import click

from .. import radiotomate_cli


async def do_update(engine, target_version=None):
    import sys
    from importlib import import_module
    from pkgutil import iter_modules

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    async with engine.connect() as conn:
        try:
            q = text("select value from settings where key=:key")
            result = await conn.execute(q, {"key": "SCHEMA_VERSION"})
            current_version = int(result.scalars().first())
        except OperationalError:  # when table does not exist
            current_version = 0

        latest_version = 0
        updates_module = sys.modules[__name__]
        for modinfo in iter_modules(updates_module.__path__):
            name = modinfo.name
            if name.startswith("v"):
                version = int(name[1:])
                latest_version = max(version, latest_version)

        if target_version is None:
            target_version = latest_version

        if target_version > latest_version:
            click.secho(
                f"Version {target_version} does not exists",
                fg="red",
                bold=True,
            )
            raise click.exceptions.Exit(1)

        if current_version < target_version:
            for i in range(current_version + 1, target_version + 1):
                click.echo(f"Applying schema change #{i}... ", nl=False)
                module = import_module(f"radiotomate.commands.update.v{i}")
                await module.upgrade(conn)
                q = text("update settings set value=:value where key=:key")
                await conn.execute(q, {"value": str(i), "key": "SCHEMA_VERSION"})
                await conn.commit()
                click.secho("OK", fg="green", bold=True)
        elif current_version > target_version:
            for i in range(current_version, target_version, -1):
                click.echo(f"Reverting schema change # {i-1}... ", nl=False)
                module = import_module(f"radiotomate.commands.update.v{i}")
                await module.downgrade(conn)
                q = text("update settings set value=:value where key=:key")
                await conn.execute(q, {"value": str(i - 1), "key": "SCHEMA_VERSION"})
                await conn.commit()
                click.secho("OK", fg="green", bold=True)
        else:
            click.secho("DB is up-to-date", fg="green", bold=True)


@radiotomate_cli.command()
@click.option(
    "--target-version",
    help="Target version (may be used to downgrade)",
    type=int,
)
@click.pass_obj
def update(config, target_version=None):
    """
    Update a Radiotomate installation
    """
    import asyncio

    # do *not* import radiotomate.models from here, because their tables may not exist
    # yet. though, we use db.init_from_config() in order to have the same PRAGMAs
    # and settings as the app
    from radiotomate.db import QuartAlchemy

    db = QuartAlchemy()
    db.init_from_config(config["db"], {})
    asyncio.run(do_update(db.engine, target_version))
    asyncio.run(db.after_serving())
