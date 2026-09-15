# load sub-commands
from radiotomate.commands import (  # noqa: F401
    install,
    interface,
    radiotomate_cli,
    scheduler,
    update,
    users,
)

if __name__ == "__main__":
    radiotomate_cli()
