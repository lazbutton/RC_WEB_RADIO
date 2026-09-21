# Architecture BUTTON

Dossiers à la racine : [`player/`](../player/README.md), [`radiotomate/`](../radiotomate/README.md), [`button/hub/`](../button/hub/). Carte : [`README.md`](../README.md). Infra et runbooks : dépôt séparé `button-ops` (`runbook-nasgul.md`).

Priorité d’antenne Radiotomate : **live > relais > carts > auto-DJ**.

## Prod réelle (septembre 2026) : Nasgul, Docker Compose

```text
GitHub (RC_WEB_RADIO, button-console, button-ops)
   │  Actions : pytest + liquidsoap --check → GHCR :main + tag daté
   ▼
Nasgul (TrueNAS) — cron 10 min nasgul-update.sh : pull, up, attente healthy, rollback + HOLD
   ├── radiotomate-playout   Liquidsoap 2.4.5  API :6833 (interne)  harbor :6800
   ├── radiotomate-scheduler :6822 (interne)  horloges, outbox, rétention, alertes
   ├── radiotomate-interface :6811  UI producteurs + API JSON console
   ├── console (React)       :30126
   ├── radiotomate-dropbox   boucle `beet import` sur /media (ReplayGain ffmpeg)
   ├── icecast local         :18000  /button.mp3   (banc / LAN)
   ├── nowplaying            :6820   now.json  ← metadata_log.relay_to
   └── catalog + meilisearch :30127
        │
        └── relais Icecast public **VPS** radio.lazbutton.xyz (`ICECAST_PUBLIC_HOST`, coupé tant que la banque est vide)

QG (laptop / Pi, BUTT) ──► harbor :6800 (rôle stream)
```

Données : `/mnt/data/apps/button/data/radiotomate` (SQLite, carts, Beets, logs) ; banque `/mnt/data/media/button` (SMB `BUTTON-Media`, Nextcloud). Snapshots ZFS + réplication.

La VM Labomedia / Podman du plan initial n’est **pas** utilisée ; `radiotomate.kube.yaml` dans `button-ops` reste une piste si Nasgul doit être remplacé.

## Ports

| Port | Service | Exposition |
|---|---|---|
| 6811 | Interface web producteurs | LAN / Tailscale |
| 6800 | Entrée live (harbor) | LAN / Tailscale |
| 6822 | Scheduler | interne (network du playout) |
| 6833 | API playout | interne |
| 6802 | PCM voiceover (micro Hub) | interne |
| 18000 | Icecast local | LAN |
| 30126 / 30127 / 6820 | Console, catalogue, now.json | LAN |
| 8000 | Icecast public VPS (source Liquidsoap) | Internet |

Tout est en HTTP sur le LAN + Tailscale ; TLS (Caddyfile dans `button-ops`) pas déployé. Ne jamais exposer 6822 / 6833.

## Santé et exploitation

- `GET /health/ready` scheduler : DB + heartbeat Liquidsoap < 5 s + playout joignable → healthcheck compose et garde du déploiement.
- `GET /metrics` scheduler : compteurs persistés dans `/data/metrics.json` (rechargés au redémarrage) + une ligne par heure dans `/data/exports/metrics.jsonl`.
- Alertes (`alerts:` dans `radiotomate.yaml`) : silence, playout injoignable, heartbeat perdu, créneau live sans encodeur (EF-01), sortie Icecast déconnectée (`icecast_down`, locale ou relais public) → log, webhook, tâche Vikunja.
- Rétention (`retention:`) : conducteur joué > 7 j, commandes acquittées > 24 h, versions de programmation orphelines, `metadata_log` > 90 j exporté en JSONL avant purge.
- Heures : tout est en **heure de Paris naïve** (`planned_at`, `metadata_log.on_air`, `created`). Les conteneurs ont `TZ=Europe/Paris` depuis le 21/09/2026 19:40 ; avant, `created`/`reserved_at`/`on_air` étaient en UTC → le `metadata_log` saute de 17:30 à 19:40 ce jour-là (2 h de « trou » apparent, pas de silence réel). Le conducteur se recale sur l'as-run à chaque passage à l'antenne (`realign_rundown`), et les items `failed` ne comptent jamais comme couverture de prévision.

## Icecast depuis un conteneur

`localhost` / `127.0.0.1` ne marchent pas. Même stack : nom du service compose (`icecast`). Public : `ICECAST_PUBLIC_HOST` dans `.env`.

## Licence

AGPL-3.0-or-later. Instance publique = dépôt source public (ce repo).
