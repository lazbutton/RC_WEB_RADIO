"""Modules métier. Chaque module expose `setup(kernel) -> module` et un `router` FastAPI.

Ajouter un module : un dossier ici avec `setup()`, une entrée dans ALL, une migration SQL.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("regie.modules")

ALL = [
    "regie.modules.mail",
    "regie.modules.contacts",
    "regie.modules.events",
    "regie.modules.planning",
    "regie.modules.calendar",
    "regie.modules.shows",
    "regie.modules.publish",
    "regie.modules.radio",
]


def load_modules(kernel, names: list[str] | None = None) -> dict[str, Any]:
    from regie.kernel.files import FileService  # noqa: F401  (import garde le noyau autonome)

    kernel.jobs.register("files.scan", lambda ctx: {"indexed": kernel.files.scan(str(ctx.payload.get("path") or ""))}, "maintenance")
    for name in names or ALL:
        try:
            package = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name and name.startswith(exc.name):
                log.info("module %s absent", name)
                continue
            raise
        setup = getattr(package, "setup", None)
        if not setup:
            continue
        module = setup(kernel)
        kernel.modules[getattr(module, "name", name.rsplit(".", 1)[-1])] = module
    return kernel.modules
