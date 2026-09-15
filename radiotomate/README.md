# 📡 Radiotomate 🍅

⚠️ this is a work in progress:

* major changes may occur
* roadmap is in TODO.md: many things are still missing ! any help would be welcome.

Stay updated by subscribing to the mailing-list:
send an (empty) e-mail to [~martink/radiotomate+subscribe@lists.sr.ht](mailto:~martink/radiotomate+subscribe@lists.sr.ht) (you may later replace `subscribe` by `unsubscribe` to unsubscribe).

🇫🇷 Vous pouvez recevoir des nouvelles du projet en rejoingant la mailing-list francophone ; envoyez un e-mail (vide) à [~martink/radiotomate-fr+subscribe@lists.sr.ht](mailto:~martink/radiotomate-fr+subscribe@lists.sr.ht)

Project resources are centralized at https://sr.ht/~martink/radiotomate/

## Developer install

You'll need a Linux box, with `tmux` and the following executables reachable from `$PATH`:
 * a recent Python (you probably already have one)
 * [Poetry](https://python-poetry.org/docs/#installation)
 * [Liquidsoap](https://www.liquidsoap.info/doc-dev/install.html)
 * npm

After cloning this repository,
 * install with `poetry install`
 * create a DB and configuration file with `poetry run radiotomate develop`
 * add yourself an admin account with `poetry run radiotomate -c radio_data/radiotomate.yaml useradd --admin USERNAME`

Then you can call
 * `eval $(poetry env activate)` before running any command manually, in perticular tests, or
 * `./dev.sh` to start the 3 processes that form the automation system:
   - `RTCONFIG=radio_data/radiotomate.yaml liquidsoap playout/radiotomate.liq`, the process that actually plays sound
   - `radiotomate interface radio_data/radiotomate.yaml`, users' Web interface
   - `radiotomate scheduler radio_data/radiotomate.yaml`, a JSON-only HTTP API interfacing the former two and playing with the sound stream according to the schedule.
   - On your first use, we recommend you start `./dev.sh` and connect to the interface to add your first sounds, in perticular a jingles cart and some music.

`radiotomate --help` will present available commands and options more exhaustively.

If using VSCode, we recommend the `savonet.vscode-liquidsoap` extension to edit the Liquidsoap script.

CSS is compiled from the `sass` folder. You'll need to `npm install` once.
When developing, use `npm run watch` or `npm run build`.
Before packaging, or after merging, run `npm run package`.

### Test

Before running tests, activate the virtual environment (`eval $(poetry env activate)`).

You'll need to run the following commands once

```
pip install pytest-playwright-asyncio
playwright install-deps
playwright install
```

Then run `pytest`.

### Before committing

Run from the source folder:

```
ruff check
ruff format
pytest
```

### Before releasing

Check version numbers in

* pyproject.toml
* sass/package.json

Build containers with
 * podman build -t radiotomate/playout playout/
 * podman build -t radiotomate/webapps .


