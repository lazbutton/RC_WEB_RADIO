import secrets
from pathlib import Path
from shutil import copy

import click

YAML_TEMPLATE = """
---

data:
    root: {data_root}

db:
    url: "sqlite+aiosqlite:///{db}"

interface:
    # should be set randomly when installing. Changing it would log out everyone.
    cookie_salt: {cookie_salt}
    # true behind TLS; false for local HTTP (develop / tests).
    cookie_secure: {cookie_secure}
    csrf_enabled: true
    # Build Vite (console/dist). Vide = chercher ../console/dist à côté du dépôt.
    # console_dist: /var/lib/radiotomate/console/dist

playout_process_config:
    input_name: "{http_input_name}"
    input_min_buffer: 5.
    input_max_buffer: 30.
    token: {playout_token}
    start_sound: {start_sound_path}
    {playout_log_config}
    outputs:
        #- driver: pulseaudio
        #  # empty(null) will let pulseaudio choose the default device.
        #  device:

        #- driver: alsa
        #  device: "default"

        #- driver: icecast
        #  encoder: vorbisvbr
        #  quality: 0.3
        #  host: "127.0.0.1"
        #  port: 9876
        #  mount: "radio.ogg"
        #  password: "secret"

metadata_log:
    # list of metadata fields that should be stored when available, in addition to
    # artist/title/album.
    extra_fields:
        - genre
        - year
    relay_retry:
        max_attempts: 3
        timeout_seconds: 2
        backoff_seconds: [0.2, 0.5]

    # if you want to POST metadata to some other websites, remove leading # below:
    #relay_to:
    #  - url: "https://website.radio/playlist/add_item"
    #    # additional POST fields
    #    add_field:
    #      SECRET_KEY: "secret value"
    #      SOURCE_NAME: "radiotomate"
    #  - url: "https://myradio.org/track_recorder.php"
    #    # and/or additional POST headers
    #    add_header:
    #      Authorization: "Basic YWxhZGRpbjpvcGVuc2VzYW1l"

health:
    heartbeat_max_age_seconds: 5

queue_cleaner:
    enabled: true
    max_age_seconds: 600
    interval_seconds: 60


############# Logging configuration ##########
logging:
    version: 1
    disable_existing_loggers: false

    formatters:
        generic:
            format: "%(asctime)s %(levelname)-5.5s [%(name)s:%(lineno)s] %(message)s"

    handlers:
        default:
            formatter: generic
            class: "logging.StreamHandler"
        interface:
            formatter: generic
            {interface_handler}
        scheduler:
            formatter: generic
            {scheduler_handler}

    root:
        level: INFO
        # load_config() will add one handler (interface or scheduler or default)
        # this allows interface and scheduler to user the same configuration while
        # outputting to their own file in production.
        handlers: []

    loggers:
        # This allows radiotomate to log@DEBUG while keeping others >= INFO
        radiotomate:
            level: {log_level}
            handlers: []
            propagate: false
            qualname: radiotomate

        # set level to INFO if you need to see requests made by Python processes
        httpx:
            level: WARNING
            qualname: httpx
"""

STARTWAV = "default_start_sound.wav"


class Installer:
    def __init__(self, path_data: Path):
        self.path_data = path_data
        self.http_input_name = "stream"

    @property
    def path_yaml(self):
        return self.path_data / "radiotomate.yaml"

    @property
    def path_db(self):
        return self.path_data / "radiotomate.db"

    @property
    def path_interface_log(self):
        return self.path_data / "interface.log"

    @property
    def path_scheduler_log(self):
        return self.path_data / "scheduler.log"

    @property
    def path_playout_log(self):
        return self.path_data / "playout.log"

    def _potential_paths(self):
        yield from [
            self.path_yaml,
            self.path_db,
            self.path_interface_log,
            self.path_scheduler_log,
            self.path_playout_log,
            self.path_data / STARTWAV,
        ]

    def instance_exists(self) -> bool:
        return any(path.exists() for path in self._potential_paths())

    def revert(self):
        for path in self._potential_paths():
            if path.exists():
                path.unlink()

    def install(self, dev=False):
        if dev:
            interface_handler = 'class: "rich.logging.RichHandler"'
            scheduler_handler = 'class: "rich.logging.RichHandler"'
            log_level = "DEBUG"
            playout_log_config = ""
        else:
            playout_log_config = f'log_path: "{self.path_playout_log}"'
            log_level = "INFO"
            scheduler_handler = f"""class: "logging.handlers.RotatingFileHandler"
            filename: "{self.path_scheduler_log}"
            maxBytes: 10000000
            backupCount: 10
            """
            interface_handler = f"""class: "logging.handlers.RotatingFileHandler"
            filename: "{self.path_interface_log}"
            maxBytes: 10000000
            backupCount: 10
            """

        click.echo(f"Initializing data folder: {self.path_data}")
        (self.path_data / "carts").mkdir(parents=True, exist_ok=True)
        start_sound = copy(Path(__file__).parent / STARTWAV, self.path_data / STARTWAV)

        with self.path_yaml.open("w") as yaml_file:
            click.echo(f"Writing configuration file: {self.path_yaml}")
            yaml_file.write(
                YAML_TEMPLATE.format(
                    data_root=self.path_data,
                    db=self.path_db,
                    http_input_name=self.http_input_name,
                    interface_handler=interface_handler,
                    scheduler_handler=scheduler_handler,
                    log_level=log_level,
                    playout_log_config=playout_log_config,
                    cookie_salt=secrets.token_hex(),
                    cookie_secure="false" if dev else "true",
                    playout_token=secrets.token_hex(),
                    start_sound_path=str(start_sound),
                ),
            )

    def recap(self):
        click.secho("\nAll done !", fg="green", bold=True)
        click.echo("Radiotomate configuration, database, logs and files will be in:")
        click.secho(str(self.path_data), bold=True)
        click.secho("\nWe advise you backup regularly the content of this folder.")
