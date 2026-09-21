# Runbook Nasgul — Régie

Machine **Nasgul** (TrueNAS). Aucun mot de passe ici.

| Élément | Valeur |
| --- | --- |
| Interface | http://nasgul.taild4714f.ts.net:30130 (Tailscale) · http://192.168.1.100:30130 (LAN) |
| Conteneurs | `regie-regie-1` (API + UI), `regie-regie-pg-1` (Postgres 18) — projet compose `regie` |
| Code déployé | `/mnt/data/apps/regie/app/regie` (copie du dossier `regie/` du dépôt) |
| Secrets | `/mnt/data/apps/regie/secrets.env` (600) — modèle `regie/secrets.env.example` |
| Données | dataset `data/apps/regie` : `pg/` (base), `data/` (cache pièces jointes, bord public en mode `dir`) |
| Médias | `/mnt/data/media/button` monté en `/media` (lecture partout, écriture `00-inbox`, `40-emissions`) |
| Sauvegardes | dataset `backup/regie` : `regie-*.sql.enc` quotidiens (30 gardés), `pre-deploy-*.sql.gz` (7 gardés) |
| Snapshots ZFS | tâches récursives sur `data` : horaire 48 h, quotidien 14 j, hebdo 8 sem. |
| Postgres externe | port 30131 sur l’IP Tailscale de Nasgul, pour le worker de transcription du Mac |

## Gestes courants

- Déployer depuis le Mac : `./regie/deploy.sh` (`NASGUL_HOST=nasgul-ts` hors LAN). Sauvegarde → build → migration → fumée → retour arrière automatique.
- Journaux : `sudo docker logs -f regie-regie-1` (JSON, une ligne par événement, `request_id`).
- Redémarrer : `cd /mnt/data/apps/regie && sudo -E docker compose --env-file secrets.env -p regie -f compose.yml restart regie`.
- Migrer à la main : `… run -T --rm --no-deps regie python -m regie migrate`.
- Sauvegarde immédiate : job `kernel.backup` (« État du système ») ou `… run -T --rm --no-deps -v /mnt/backup/regie:/backups regie python -m regie backup /backups`.
- Restaurer sur le Mac (exercice trimestriel) : `REGIE_SECRET=… ./regie/scripts/restore-drill.sh`.
- Export complet : `… run -T --rm --no-deps -v /mnt/backup/regie:/backups regie python -m regie export /backups`.
- Reprise Inbox Zero (déjà faite le 21/09/2026) : `python -m regie import-inboxzero /import/inboxzero.db` avec `/mnt/data/apps/inboxzero/data` monté en `/import`.

## Retrait des anciens outils

- **Inbox Zero** (`ix-inboxzero`, port 30128) : données reprises dans Régie ; garder l’app une semaine en lecture, puis `midclt call app.stop inboxzero`. Le dataset `data/apps/inboxzero` reste snapshoté.
- **Vikunja** (`ix-vikunja`, port 30107) et **vikunja-ics** : remplacés par Planning + Agenda. Exporter les tâches ouvertes de Vikunja (projet Radio) dans Régie, puis `midclt call app.stop vikunja` et retirer le cron `vikunja-ics`. Décision explicite de l’équipe avant l’arrêt.

## Ce qui reste à fournir

Voir le tableau des connecteurs dans `regie/README.md` : Google OAuth (client id/secret), WordPress (mot de passe d’application), bord public (Vercel ou Cloudflare Pages), VAPID, Icecast.
