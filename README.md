# BUTTON

Dépôt technique de la webradio du [New Trad Fest](https://newtradfest.com).  
Automate de production : dérivé de **[Radiotomate](https://radiotomate.org/)** (AGPL-3.0-or-later), vendored dans ce dépôt.  
Flux public : `https://streams.labomedia.org:8443/` (mount à confirmer avec Labomedia).

Un seul dépôt GitHub (`lazbutton/RC_WEB_RADIO`). Commencer par le README du dossier concerné.

| Projet | Dossier | Rôle |
|---|---|---|
| Player public | [`player/`](player/README.md) | Site auditeur : lecture du flux + titre en cours |
| Kiosk Aero | [`kiosk/`](kiosk/README.md) | App Android 24/24 (ExoPlayer, Face nappe) |
| Console producteurs | [`radiotomate/`](radiotomate/README.md) | Automate BUTTON (dérivé Radiotomate, AGPL) — UI, carts, live, auto-DJ |
| Infra | [`ops/`](ops/README.md) | Icecast, Caddy, YAML Labomedia, banc Mac, runbook |
| Proto automate (DA) | [`console/`](console/README.md) | Squelette horloges Radix — **pas** la prod `:6811` |
| Régie (hub d’équipe) | [`regie/`](regie/README.md) | Mails, contacts, événements Outlive, planning + Google Agenda, émissions/podcasts, publication, vie de la radio — sur Nasgul |
| Blocs publics | [`rco-site/`](rco-site/README.md) | Bord statique (iframes, RSS) publié par Régie pour orleans.radiocampus.org |
| Nasgul | [`nasgul/`](nasgul/runbook-regie.md) | Runbooks TrueNAS : Régie, Inbox Zero (repris), Vikunja (à arrêter) |

Docs transverses : [`docs/`](docs/README.md) (architecture, comptes, live QG).  
Ancien boîtier Pi « radio complète » : [`docs/archive/`](docs/archive/README.md) — **pas** la prod.

## Où travailler

- **Son et antenne** → `radiotomate/` (interface `:6811`). Le playout Liquidsoap ne tourne vraiment que sous Linux (Podman + systemd).
- **Écoute / DA site** → `player/`
- **Hub d’équipe (mails, contacts, planning, podcasts)** → `regie/` (`http://nasgul.taild4714f.ts.net:30130`)
- **Déploiement Labomedia ou banc local** → `ops/`
- **Squelette horloges (hors moteur)** → `console/` (`http://127.0.0.1:5175/`) — proto de DA, pas Quart.

## Lancer le banc Mac

Recette complète : [`ops/banc-mac.md`](ops/banc-mac.md). En bref :

```bash
cd ops/local && docker compose up --build -d
cd ../../player && npm install && npm run dev   # http://127.0.0.1:5174/
cd ../../radiotomate && poetry run radiotomate interface --demo   # UI http://127.0.0.1:6811
```

Radiotomate **complet** (auto-DJ, harbor live `:6800`) ne tourne pas sur macOS.

## Hors sujet (ne pas mélanger)

- **LazCast** : autre projet, pas ce dépôt.
- **Pi 3B en automate 24/24** : cahier archivé. Le Pi n’est éventuellement qu’un **encodeur live** vers le port 6800 ([`docs/live-qg.md`](docs/live-qg.md)).

Licence : **AGPL-3.0-or-later** ([`LICENSE`](LICENSE)). Instance réseau = ce dépôt (ou un fork) doit rester public.
