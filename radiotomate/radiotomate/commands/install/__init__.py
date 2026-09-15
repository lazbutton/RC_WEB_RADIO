"""
Radiotomate instance creation commands
======================================

Those commands can create a configuration file, a database, and also install
system services.
"""

from pathlib import Path

import click

from .. import load_config, radiotomate_cli
from ..update import update


@radiotomate_cli.command()
@click.option(
    "--no-revert-on-failure",
    is_flag=True,
    help="If you want to develop/debug this installer",
)
@click.option(
    "-d",
    "--data-root",
    help="Path to radiotomate's data folder",
    type=click.Path(
        allow_dash=False,
        exists=False,
        resolve_path=True,
        path_type=Path,
    ),
)
@click.pass_context
def install(ctx, no_revert_on_failure, data_root: click.Path | None = None):
    """
    Set-up a new Radiotomate installation
    """
    from .installer import Installer

    installer = None
    try:
        click.secho("\nWelcome to the Radiotomate installer!", fg="green", bold=True)
        click.echo(
            "\nHit Ctrl+C to abort. Press Enter "
            "to use the default value (shown between square brackets).\n"
            "",
        )

        if not data_root:
            default = str(Path("./radio_data").absolute())
            data_root = Path(
                click.prompt(
                    "In which folder should Radiotomate store all its data?",
                    default=str(default),
                )
            )

        installer = Installer(data_root.absolute())
        if installer.instance_exists():
            click.secho(
                "Note: it seems an instance already exists in this folder, "
                "we'll only try to update it",
                bold=True,
                fg="red",
            )
        else:
            installer.install()

        ctx.params["config_path"] = installer.path_yaml
        ctx.obj = load_config(installer.path_yaml)
        ctx.invoke(update)

    except Exception:
        if installer and not no_revert_on_failure:
            installer.revert()
        raise

    installer.recap()


@radiotomate_cli.command()
@click.option(
    "--quiet",
    is_flag=True,
    help="do not print the recap when done - useful for testing",
)
@click.pass_context
def develop(ctx, quiet):
    """
    Set-up a new Radiotomate installation for development or testing (create only the
    database and the configuration file)
    """
    from .installer import Installer

    target_path = Path("./radio_data").absolute()
    installer = Installer(target_path)
    if installer.instance_exists():
        raise click.UsageError(
            f"It seems a development install already exists in {target_path}"
        )
    installer.install(dev=True)

    ctx.params["config_path"] = installer.path_yaml
    ctx.obj = load_config(installer.path_yaml, verbose=True)
    ctx.invoke(update)

    if not quiet:
        click.secho("\nRadiotomate is installed for development", fg="green", bold=True)
        click.echo("We advise you create an admin user using:")
        click.echo(
            f"    radiotomate -c {installer.path_yaml} useradd --admin [USERNAME]"
        )
        click.echo(
            "Then, you can call ./dev.sh to start the system: log in, create a "
            "jingles cart (put at least one sound), and add some music."
        )
