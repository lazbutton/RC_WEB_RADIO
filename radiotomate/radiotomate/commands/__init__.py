"""
Radiotomate CLI
===============

All commands are implemented with Click, grouped as ``@radiotomate_cli.command()``, each
command in a sub-module. Do not forget to import the submodule in ``main``.

The radiotomate_cli group parses the configuration and configures loggers.
"""

from collections.abc import Mapping
from pathlib import Path

import click


@click.group()
@click.option(
    "-c",
    "--config-path",
    envvar="RTCONFIG",
    type=click.Path(
        readable=True,
        allow_dash=False,
        exists=True,
        resolve_path=True,
        path_type=Path,
    ),
    help=(
        "Path to the configuration file. "
        "You can also export this path in the RTCONFIG environment variable "
        "(this is recommended for regular use)."
    ),
)
@click.option("--verbose", is_flag=True, help="Sets logging level to DEBUG.")
@click.version_option()
@click.pass_context
def radiotomate_cli(ctx, verbose, config_path=None):
    main_handler = None
    if ctx.invoked_subcommand in ("interface", "scheduler"):
        main_handler = ctx.invoked_subcommand
    ctx.obj = LazyConfigLoader(config_path, verbose, main_handler)


class LazyConfigLoader(Mapping):
    """
    This should be used as Click's context object, to lazyly call `load_config`: it
    tries to load the config only when the command actually tries to read the dict.

    This avoids raising an error saying config path is mandatory in commands that don't
    need it (like intall or --help)
    """

    def __init__(self, config_path, verbose, main_handler):
        self.config: dict = None
        self._config_path = config_path
        self._verbose = verbose
        self._main_handler = main_handler

    def _ensure_loaded(self):
        if not self.config:
            if not self._config_path:
                raise click.UsageError(
                    "Please set the RTCONFIG environment variable, or use:\n"
                    "    radiotomate -c CONFIG_PATH subcommand..."
                )
            self.config = load_config(
                self._config_path, self._verbose, self._main_handler
            )

    def __getitem__(self, key):
        self._ensure_loaded()
        return self.config[key]

    def __iter__(self):
        self._ensure_loaded()
        return iter(self.config)

    def __len__(self):
        self._ensure_loaded()
        return len(self.config)


def load_config(config_path, verbose=False, main_handler=None) -> dict:
    import logging.config

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")
    with config_path.open() as f:
        config = yaml.load(f)

    if verbose:
        config["logging"]["loggers"]["radiotomate"]["level"] = "DEBUG"

    if not main_handler:
        main_handler = "default"
    config["logging"]["root"]["handlers"].append(main_handler)
    config["logging"]["loggers"]["radiotomate"]["handlers"].append(main_handler)

    logging.config.dictConfig(config["logging"])

    return config
