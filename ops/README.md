# Ops — Icecast, Caddy, Labomedia, banc

Tout ce qui n’est pas du code produit (player, grille, fork Radiotomate) : déploiement, secrets d’exemple, banc Docker.

| Fichier | Usage |
|---|---|
| [`banc-mac.md`](banc-mac.md) | **Allumer le banc** sur ce Mac |
| [`runbook-labomedia.md`](runbook-labomedia.md) | Install / restart / logs sur la VM |
| [`Caddyfile`](Caddyfile) | TLS → UI `:6811` + live `:6800` ; player optionnel |
| [`radiotomate.yaml.example`](radiotomate.yaml.example) | Icecast Labomedia + `relay_to` Now Playing |
| [`radiotomate.kube.yaml`](radiotomate.kube.yaml) | Manifest pod (si pas `install.sh` quadlet) |
| [`radiotomate.kube.service`](radiotomate.kube.service) | Quadlet systemd user |
| [`secrets.env.example`](secrets.env.example) | Mots de passe, tokens — ne jamais commiter le vrai fichier |
| [`local/`](local/README.md) | Compose Icecast `:18000` + nowplaying `:6820` |
| [`scripts/setup-dropbox.sh`](scripts/setup-dropbox.sh) | Taxonomie MusicDropbox |
| [`scripts/apply-button-brand.sh`](scripts/apply-button-brand.sh) | `INTERFACE_NAME` en base |

Prod : suivre [install.html](https://radiotomate.org/install.html) **sur la VM Linux**, puis fusionner les extraits YAML d’ici (ne pas écraser `cookie_salt` / `token` générés).
