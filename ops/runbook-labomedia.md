# Runbook Labomedia

## Gate avant install

- Compte SSH sur la VM Debian/Ubuntu dédiée (Olmo / Labomedia).
- Quota disque 100 Go–1 To pour `radiotomate_data/`.
- Mount Icecast NTR (`/ntradio.mp3` ou nom Labomedia) + mot de passe source.
- Ports 6811 et 6800 libres en local ; 80/443 pour Caddy.
- `loginctl enable-linger $USER` après le premier `systemctl --user start`.

## Install

```bash
sudo apt update
sudo apt install -y podman fuse-overlayfs caddy
loginctl enable-linger "$USER"
export DATA_ROOT="$HOME/radiotomate_data"
export ASK_FOR_CONFIRMATION=no
# ADMIN_PASSWORD depuis un gestionnaire de secrets, pas l’historique shell
wget -O - https://radiotomate.org/install.sh | bash
```

Fusionner [radiotomate.yaml.example](radiotomate.yaml.example) **sans** écraser `cookie_salt` ni `token`.

```bash
systemctl --user restart radiotomate.service
systemctl --user enable radiotomate.service
```

Copier [Caddyfile](Caddyfile), adapter le hostname, `systemctl reload caddy`.

## Dossiers médias

```bash
./ops/scripts/setup-dropbox.sh "$HOME/radiotomate_data/MusicDropbox"
```

Premier jingle dans l’UI (cart jingles), puis MP3 dans `MusicDropbox/rotation/`.

## Marque NTR

```bash
./ops/scripts/apply-ntr-brand.sh "$HOME/radiotomate_data/radiotomate.db"
systemctl --user restart radiotomate.service
```

Si le code est le fork `ntr/theme`, `INTERFACE_NAME` est déjà posé à l’init.

## Commandes

```bash
systemctl --user status radiotomate
systemctl --user restart radiotomate
journalctl --user -u radiotomate -f
# logs applicatifs
tail -f ~/radiotomate_data/playout.log ~/radiotomate_data/interface.log
```

## Sauvegarde

`radiotomate_data/` entier (YAML, sqlite, Beets, Music, carts). Pas besoin des images OCI.

## Player public

Copier le **build** [`player/dist/`](../player/) (`npm run build` dans `player/`) vers `/var/www/ntradio-public`. Dans `config.js`, mettre `streamUrl` = `productionStreamUrl`. Caddy : second bloc de [`Caddyfile`](Caddyfile).

## Ancien automate Pi

Hors prod. Fichiers dans [`docs/archive/pi-boitier/`](../docs/archive/pi-boitier/README.md). Le service `radio.service` sur `rc-web-radio.local` n’alimente plus Icecast public. Le Pi ne sert, au mieux, que d’encodeur live ([docs/live-qg.md](../docs/live-qg.md)).
