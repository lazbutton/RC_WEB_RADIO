import click

from . import radiotomate_cli


@radiotomate_cli.command()
@click.option(
    "--reload",
    is_flag=True,
    help="Reloads app on changes (when developing)",
)
@click.pass_obj
def scheduler(config_dict, reload):
    """
    Start the Radiotomate scheduler service
    """
    import asyncio

    from hypercorn.asyncio import serve

    from radiotomate.beets import BeetsIntegration
    from radiotomate.quart import CustomQuart, ShutdownManager
    from radiotomate.scheduler.watchdog import Watchdog
    from radiotomate.scheduler_app import app_factory

    beets = BeetsIntegration.default()
    app = app_factory(config_dict, beets)

    Watchdog(app)

    if reload:
        app.run(use_reloader=reload, host="0.0.0.0", port=6822)
    else:

        @app.before_serving
        async def on_start():
            ShutdownManager().setup_signal_handler()

        config = CustomQuart.make_basic_hypercorn_config()
        config.bind = ["0.0.0.0:6822"]
        asyncio.run(serve(app, config, shutdown_trigger=ShutdownManager().wait))
