# BUTTON — Radiotomate

Ce dossier fait partie du monorepo **RC_WEB_RADIO**. 

Code dérivé de [Radiotomate](https://radiotomate.org/) (Martin Kirchgessner), **AGPL-3.0-or-later**. Le `README.md` de ce dossier reste celui de l’amont.

Libellés UI : [`button/brand.json`](../button/brand.json) (`INTERFACE_NAME` au boot). Inventaire : [`docs/radiotomate-inventaire.md`](../docs/radiotomate-inventaire.md).

## Patches (volontairement minces)

Ne pas réécrire `playout/radiotomate.liq`.

- `INTERFACE_NAME` / login : brand `labels.automate`
- `beets/compat.py` : ioctl TIOCGWINSZ 8 octets (Python 3.14 + Beets 2.5)
- `layout.jinja` : `lang=fr`, logo, `suite.css`
- `sass/_suite.scss` + `static/suite.css` : charte N&B, pupitre sombre

## Moteur horloges et exploitation (sept. 2026)

Scheduler (`radiotomate/scheduler/`) :

- `clock.py` tick ; helpers purs dans `timing.py` (Paris, ancres, `remaining`) et `music_rules.py` (séparation artistes / titres). `execution.py` = prévision + réconciliation as-run ; `desk.py` = édition pupitre.
- Le tick 1 Hz de Liquidsoap n’exécute l’horloge que si l’état a changé, à la minute, ou à moins de 10 s de la fin du titre (`live.should_run_tick`, compteur `tick_gated_total`).
- `retention.py` : purge bornée (conducteur joué, commandes acquittées, versions orphelines) et export JSONL de `metadata_log` (`data/exports/`). Config `retention:`.
- `metrics.py` : `/metrics` persisté dans `data/metrics.json`, une ligne / heure dans `data/exports/metrics.jsonl`. Config `metrics:`.
- `alerts.py` : silence, playout injoignable, heartbeat → log, webhook, tâche Vikunja. Config `alerts:`.
- ReplayGain : un titre Beets sans `rg_track_gain` est analysé à la prévision (`analyze_item_soon`) puis au besoin au push (`ensure_item_gain`) ; plus de push sans gain.
- Carts : mode `CLOCK` (défaut) = joué par les horloges et les pads, sans cron APScheduler ; migration v13 bascule les crons par défaut `* :00`.

Liquidsoap (`playout/radiotomate.liq`, LS 2.4.5) : `radiotomate_queue` annoté sur chaque push (source as-run fiable), `/queue/flush` à corps optionnel `{"queues": […]}`, `relay_url` configurable. Vérifier : `RTCONFIG=… liquidsoap --check playout/radiotomate.liq` (fait en CI).

## Démo sur ce Mac (UI seulement)

```bash
export PATH="/opt/homebrew/bin:$PATH"
poetry install
poetry run radiotomate develop
poetry run radiotomate -c radio_data/radiotomate.yaml users add lazbutton --admin
poetry run radiotomate -c radio_data/radiotomate.yaml interface --demo --reload
```

- UI HTMX : http://127.0.0.1:6811
- Console admin : repo `button-console` (http://127.0.0.1:5175/)

Données locales (gitignorées) : `radio_data/`.

Infra : [button-ops](https://github.com/lazbutton/button-ops).
