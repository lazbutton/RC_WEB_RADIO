# Inbox Zero — Nasgul

Trieur IMAP pour la boîte Roundcube Radio Campus (`mail.radio-campus.org`).
Ce n’est **pas** le produit elie222/inbox-zero (Gmail/Outlook seulement).

- UI : http://192.168.1.100:30128 (SPA, même conteneur). Hors LAN : http://nasgul.taild4714f.ts.net:30128
- Webmail : https://webmail.radiocampus.org
- Secrets : `~/.truenas-secrets/inboxzero.json` (hors git). Jeton Notion dans Réglages.
- Clone git Nasgul (Cursor SSH) : `/mnt/data/apps/dev/RC_WEB_RADIO`

## Ce que ça fait

- **Relevé** toutes les 3 min : les mails INBOX des **30 derniers jours** passent par le tri Claude (À faire / En attente / À lire / Newsletters / Spam probable). Récap Claude à l’ouverture d’un mail, pré-récap des 5 premiers À faire.
- **Recherche** instantanée sur **toute** la INBOX (index FTS5 local : expéditeur, objet, corps, récap, noms de PJ ; préfixes, accents ignorés). Un mail plus vieux que 30 jours peut être adopté depuis la recherche : « Récap » ou « Mettre à trier ».
- **Actions IMAP réversibles** : Lu / Non lu, Drapeau, **Archiver** (déplace vers `Inbox Zero/<Catégorie>`, visible dans le webmail), Plus tard (sort de la file, reste dans INBOX). Chaque action est journalisée et annulable (toast « Annuler », Historique, touche `z`). MOVE + UIDPLUS côté serveur ; retour par COPYUID ou Message-ID.
- **Jamais** d’envoi (SMTP), jamais de suppression. Lecture en PEEK ; l’option « Marquer lu à l’ouverture » est désactivée par défaut.
- Pièces jointes : liste au relevé, ouverture à la demande (PDF / images / audio contrôlé). Notion : tâche dans Tâches (Programmé, Radio Campus) avec les pièces du fil.
- Cowork : `GET /api/feed.md` et `/api/feed.atom` (À faire / En attente). Cookie ou `Authorization: Bearer <jeton UI>`. Tailscale only.

## Raccourcis

`j` `k` naviguer · `e` archiver · `u` lu / non lu · `s` drapeau · `l` plus tard · `n` Notion · `c` copier l’exemple · `x` sélectionner · `⇧E` archiver la sélection · `z` annuler · `r` (Historique) remettre · `/` chercher · `⌘K` palette · `g` À trier / Historique · `?` aide.

## Architecture

- `web.py` : API FastAPI. `/api/queue` en une transaction SQLite avec ETag ; `/api/search` ; `/api/actions` (+ `/undo`) ; `/api/events` (SSE : `item`, `queue`, `job`, `action`, `scan`, `index`) ; jobs lourds en `202 {job_id}` (`?wait=1` pour synchrone).
- `jobs.py` : deux voies (`imap`, `llm`), file à priorité (actions P0, récap/pièces/Notion P1, relevé P2, index P3). Un relevé long rend la main entre deux lots quand une action utilisateur attend.
- `imaputil.py` : `MailSession` (une connexion persistante, reconnexion, `STORE` sans expunge, `MOVE` avec COPYUID).
- `worker.py` : handlers des jobs, tri Claude (lot Haiku clippé à 600, escalade vers le modèle fort), synchro des drapeaux toutes les 60 s, détection des mails partis de INBOX (`gone`).
- `db.py` : SQLite WAL, connexion par thread, champs pré-calculés (`excerpt_clean`, `summary_full`), tables `items`, `mail_index` + `mail_fts`, `jobs`, `actions`, `sender_memory`.
- Frontend : `ui/` (Vite / React 19, sans bibliothèque UI). Store SSE optimiste dans `src/lib/store.tsx`, pages `src/pages/queue/`, kit `src/components/`, styles `src/styles/`.

## Dev et déploiement

Tests : `.venv/bin/pytest -q` (boîte IMAP simulée dans `tests/fake_imap.py`). UI : `cd ui && npm run build`.

Dev Mac : `PYTHONPATH=src INBOXZERO_TOKEN=… INBOXZERO_DATA=/tmp/iz .venv/bin/python -m uvicorn inboxzero.web:app --port 30128` + `npm run dev` dans `ui/` (proxy `/api`).

Déploiement Nasgul (Mac ou Cursor SSH) : `./nasgul/inbox/deploy.sh` → image `nasgul-inboxzero:local`, app custom TrueNAS via `compose.yml`.
