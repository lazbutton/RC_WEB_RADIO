# Inventaire Radiotomate (miroir git)

Commit : `130030b` (`radiotomate/`, remote `https://git.sr.ht/~martink/radiotomate`).

Source canonique plus récente : Mercurial [foss.heptapod.net/radiotomate/radiotomate](https://foss.heptapod.net/radiotomate/radiotomate/).

Licence : AGPL-3.0-or-later (`radiotomate/LICENSE`). Notes NTR : [`radiotomate/NTR.md`](../radiotomate/NTR.md).

## Processus (`dev.sh` / pod)

| Processus | Commande | Port |
|---|---|---|
| Playout | `RTCONFIG=… liquidsoap playout/radiotomate.liq` | Harbor **6800** |
| Interface | `radiotomate interface` | **6811** (HTML/HTMX, auth cookie) |
| Scheduler | `radiotomate scheduler` | **6822** (JSON, `X-Auth-Token`) |
| Dropbox | `beet dropbox` | — (`MusicDropbox/`) |

## Interface (producteurs)

Blueprints Quart (package Python `radiotomate/interface/`, dans ce clone) :

- `/` live (SSE `/live`, login)
- `/carts` carts / émissions / relais
- `/autodj` créneaux + filtres Beets
- `/users` admins
- `/login`

SASS : `sass/radiotomate.scss` (Bulma) → `npm run build`. Overlay NTR : `sass/_ntr.scss` et `static/ntr.css`.

`INTERFACE_NAME` est un réglage **base** (`settings`), pas le YAML 0.1.0. La branche `ntr/theme` le pose à `New Trad Radio`.

## Scheduler (interne)

Auth : header `X-Auth-Token` = `playout_process_config.token`.

| Méthode | Chemin | Rôle |
|---|---|---|
| GET SSE | `/live` | Métadonnées en cours (artist, title, source, remaining, elapsed) |
| DELETE | `/live` | Skip |
| POST | `/live` | Push métadonnées depuis Liquidsoap |
| PUT | `/schedule/{cart_id}` | Recalcul horaire cart |
| POST | `/analyzer` | Loudness fichiers |
| POST | `/metadata_log` | Historique + `relay_to` HTTP |
| POST | `/can_stream` | Auth encodeur live (user/password + rôle stream) |

Le site **public** ne parle pas au scheduler (token secret). Now Playing = Icecast `status-json.xsl` **ou** `metadata_log.relay_to` vers `ops/local/nowplaying.py` (`GET /now.json`).

## Playout

`playout/radiotomate.liq` — sorties YAML `outputs[]` (`icecast`, `pulseaudio`, `alsa`). Entrée live `input_name` (défaut `stream`) sur 6800.

## Install prod

[install.sh](https://radiotomate.org/install.html) : Podman, quadlets, `systemctl --user`, `loginctl enable-linger`. Pas de systemd user sur macOS : banc local = compose Icecast + `interface --demo` (Linux/Docker).
