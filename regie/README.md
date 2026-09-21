# Régie — hub d’équipe Radio Campus Orléans

Régie relie ce qui était éclaté : la boîte mail (Inbox Zero, désormais module Mails), les contacts, les événements Outlive, le planning et les agendas Google, les émissions et podcasts sur le NAS, la publication vers le site, et la vie de la radio (invités, studio, volontaires, partenariats). Tout tourne sur Nasgul, dans deux conteneurs (`regie-pg`, `regie`), joignables uniquement par Tailscale : `http://nasgul.taild4714f.ts.net:30130`.

Plan de référence : [docs/regie-radio-campus.md](../docs/regie-radio-campus.md). Décisions : [docs/adr](docs/adr/).

## Architecture en une minute

- **Noyau** (`src/regie/kernel`) : comptes et rôles, registre d’entités, liens polymorphes, journal d’actions réversibles, jobs persistants avec bail, planification, outbox, connecteurs avec coupe-circuit, recherche unifiée (Postgres `tsvector` + `unaccent`), notifications (SSE + Web Push), fichiers NAS à liste blanche, observabilité. Aucun métier.
- **Modules** (`src/regie/modules`) : `mail`, `contacts`, `events`, `planning`, `calendar`, `shows`, `publish`, `radio`. Un module ne parle aux autres qu’à travers le noyau.
- **Connecteurs** (`src/regie/connectors`) : IMAP, Outlive, Google Agenda, WordPress, bord public (Vercel / Cloudflare Pages / dossier). Chacun a un faux jumeau pour les tests.
- **Interface** (`ui/`) : React 19, une coquille à barre latérale, palette ⌘K qui cherche partout, temps réel par SSE, actions annulables.
- **Base** : Postgres 18 dédié, migrations SQL numérotées dans `migrations/`, jamais modifiées après déploiement.

## Développer

```bash
cd regie
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q                 # Postgres jetable automatique (Homebrew postgresql@17)
cd ui && npm install && npm run dev  # proxy vers 127.0.0.1:30130
```

Types TypeScript générés depuis OpenAPI : `python -m regie openapi > openapi.json && (cd ui && npm run types)`.

## Exploiter

- Déployer : `./regie/deploy.sh` (ou `NASGUL_HOST=nasgul-ts ./regie/deploy.sh` hors LAN). Sauvegarde avant migration → build → migration → fumée → retour à l’image précédente si la fumée échoue.
- Secrets : `/mnt/data/apps/regie/secrets.env` (modèle : `secrets.env.example`). Jamais dans le dépôt.
- Sauvegardes : `pg_dump` chiffré (AES-GCM, clé dérivée de `REGIE_SECRET`) chaque jour dans `/mnt/backup/regie` (30 gardées) + snapshots ZFS horaires/quotidiens/hebdomadaires du dataset `data/apps/regie`.
- Restauration : `REGIE_SECRET=… ./regie/scripts/restore-drill.sh` rejoue la dernière sauvegarde dans un Postgres jetable sur le Mac et vérifie les migrations. À faire chaque trimestre.
- Export complet : `python -m regie export DEST` (un JSON par table, sans secrets, plus la liste des fichiers NAS).
- Reprise d’Inbox Zero : `python -m regie import-inboxzero inboxzero.db` (idempotent).
- Worker Mac (transcription) : voir [docs/worker-mac.md](docs/worker-mac.md).
- État : page « État du système » dans l’interface, `/health` (public, minimal), `/api/v1/metrics`.

## Comptes

Un compte par personne (`Réglages → Équipe et droits`). Rôles `admin`, `membre`, `invite`, droits `none | read | write | admin` par module. Le premier admin est créé au démarrage depuis `REGIE_BOOTSTRAP_ADMIN_*` ; changer son mot de passe dans « Mon compte ».

## Ce qu’il faut fournir pour activer chaque connecteur

| Connecteur | Variables | Où |
| --- | --- | --- |
| IMAP (Mails) | `IMAP_USER`, `IMAP_PASSWORD` | secrets.env (déjà repris d’Inbox Zero) |
| Claude (tri, récaps) | `ANTHROPIC_API_KEY` | secrets.env (repris) |
| Outlive | `OUTLIVE_ANON_KEY` (en place) ou `OUTLIVE_PARTNER_KEY` | secrets.env |
| Google Agenda | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (projet Google Cloud, écran de consentement « interne » ou test, URI de redirection = `REGIE_PUBLIC_URL/api/v1/calendar/google/callback`) | secrets.env, puis « Planning → Semaine → Connecter » |
| WordPress | `WORDPRESS_USER`, `WORDPRESS_APP_PASSWORD` (mot de passe d’application) | secrets.env |
| Bord public | `EDGE_PROVIDER=vercel` + `EDGE_TOKEN` + `EDGE_PROJECT` (ou `cloudflare` + `EDGE_ACCOUNT_ID`, ou `dir`) et `publish.public_url` dans Réglages | secrets.env + Réglages → Connecteurs |
| Web Push | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (`vapid --gen`) | secrets.env |
| Icecast | `radio.icecast_url` | Réglages → Connecteurs |

## Guides

- [Ajouter un module](docs/ajouter-un-module.md) · [Ajouter un connecteur](docs/ajouter-un-connecteur.md)
- [Registre des traitements](docs/registre-traitements.md) · [Revue de sécurité](docs/securite.md)
- [Worker Mac](docs/worker-mac.md)
