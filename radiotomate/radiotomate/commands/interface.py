import click

from . import radiotomate_cli


@radiotomate_cli.command()
@click.option("--demo", is_flag=True, help="Runs in demo mode")
@click.option(
    "--reload",
    is_flag=True,
    help="Reloads app on changes (when developing))",
)
@click.pass_obj
def interface(config_dict, demo, reload):
    """
    Start the Radiotomate web application
    """
    import asyncio

    from hypercorn.asyncio import serve

    from radiotomate.interface_app import app_factory
    from radiotomate.quart import CustomQuart, ShutdownManager
    from radiotomate.scheduler_api import Scheduler

    if demo:
        from radiotomate.beets.demo import BeetsMockIntegration

        beets = BeetsMockIntegration()
    else:
        from radiotomate.beets import BeetsIntegration

        beets = BeetsIntegration.default()

    app = app_factory(config_dict, reload, beets)
    Scheduler.init(config_dict, app, demo=demo)
    if reload:
        app.run(use_reloader=reload, host="0.0.0.0", port=6811)
    else:

        @app.before_serving
        async def on_start():
            ShutdownManager().setup_signal_handler()

        config = CustomQuart.make_basic_hypercorn_config()
        config.bind = ["0.0.0.0:6811"]
        asyncio.run(serve(app, config, shutdown_trigger=ShutdownManager().wait))

    if demo:
        beets.teardown()
