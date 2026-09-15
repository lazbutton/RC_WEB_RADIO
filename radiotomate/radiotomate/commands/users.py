import click

from . import radiotomate_cli


@radiotomate_cli.group()
def users():
    """
    Users management commands
    """


@users.command()
def perms():
    """
    List permissions identifiers, that can be used with --can
    """
    from radiotomate.models.user import PERMISSION_NAMES

    for perm in PERMISSION_NAMES:
        click.echo(perm)


PERMS_HELP = (
    "Set permission. List available permissions with the `perms` command. "
    "Repeat the option for each permissions you want to grant."
)


def _process_can(user, can):
    from radiotomate.models.user import PERMISSION_NAMES

    permissions = {}
    for permission in can:
        if permission not in PERMISSION_NAMES:
            msg = (
                f"Permission name '{permission}' does not exists. "
                "Call `radiotomate user perms` to list possible values."
            )
            click.UsageError(msg)
        permissions["can_" + permission] = "true"
    user.update_permissions(permissions)


@users.command()
@click.argument("username")
@click.password_option()
@click.option("--admin", is_flag=True, help="Give admin rights to this account")
@click.option("--can", multiple=True, help=PERMS_HELP)
@click.pass_obj
def add(config, username, password, can, admin=False):
    """
    Create a radiotomate user account
    """
    import asyncio

    from radiotomate.db import QuartAlchemy
    from radiotomate.models import User

    db = QuartAlchemy()
    db.init_from_config(config["db"], {})
    user = User(username=username)
    user.update_password(password)

    if admin or "admin" in can:
        user.update_permissions({"can_admin": "true"})
    else:
        _process_can(user, can)

    async def do_add(session):
        async with session.begin() as dbsession:
            dbsession.add(user)

    asyncio.run(do_add(db.session))


@users.command()
@click.argument("username")
@click.password_option(
    prompt_required=False,
    help="If you prefer entering it interactively, "
    "write `--password` alone after the username.",
)
@click.option(
    "--can",
    multiple=True,
    help=PERMS_HELP + "Existing permissions are untouched if the option is not used, "
    "but once one is provided other permissions will be revoked.",
)
@click.pass_obj
def mod(config, username, password, can):
    """
    Change the password of a radiotomate user
    """
    import asyncio

    from radiotomate.db import QuartAlchemy
    from radiotomate.models import User

    db = QuartAlchemy()
    db.init_from_config(config["db"], {})

    async def do_mod(session):
        async with session.begin() as dbsession:
            user = await User.from_username(dbsession, username)
            if user:
                if password:
                    user.update_password(password)
                if can:
                    _process_can(user, can)
            else:
                click.echo(f"User {username} not found")
                raise click.exceptions.Exit(1)

    asyncio.run(do_mod(db.session))


@users.command()
@click.argument("username")
@click.pass_obj
def rm(config, username):
    """
    Remove a radiotomate user account
    """
    import asyncio

    from radiotomate.db import QuartAlchemy
    from radiotomate.models import User

    db = QuartAlchemy()
    db.init_from_config(config["db"], {})

    async def do_mod(session):
        async with session.begin() as dbsession:
            user = await User.from_username(dbsession, username)
            if user:
                await dbsession.delete(user)
            else:
                click.echo(f"User {username} not found")
                raise click.exceptions.Exit(1)

    asyncio.run(do_mod(db.session))
