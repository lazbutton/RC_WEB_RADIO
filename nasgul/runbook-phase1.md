# Runbook Nasgul — Phase 1

Machine **Nasgul**, TrueNAS CE 25.10.7 (Goldeye), LAN `192.168.1.100`.
Ce fichier ne contient aucun mot de passe, aucune clé ZFS, aucun jeton.

Les secrets (clés de pools, export de config, identifiants Nextcloud) sont uniquement dans `~/.truenas-secrets/` sur le Mac, hors git. À recopier dans le gestionnaire de mots de passe.

## Accès

| Service | URL / commande | Notes |
| --- | --- | --- |
| UI TrueNAS | https://192.168.1.100 | HTTPS forcé. Certificat auto-signé **expiré** : avertissement navigateur attendu. Compte `truenas_admin`. |
| SSH | `ssh nasgul` | Utilisateur `truenas_admin`, clé `~/.ssh/id_ed25519` uniquement (`PasswordAuthentication` off). |
| Nextcloud | http://192.168.1.100:30125 | Compte `admin`. Version 34.0.4, chart 2.3.65. Pas de Collabora / Imaginary. |
| Finder (SMB) | `smb://192.168.1.100/Nextcloud` | Compte `laz`. Mot de passe dans `~/.truenas-secrets/smb-finder.json`. Pas d’invité, LAN uniquement. Plus rapide que l’upload web. |
| Banque sons (SMB) | `smb://192.168.1.100/BUTTON-Media` | Même compte `laz`. Arbre `00-inbox` … `30-habillage`. |
| Catalogue | http://192.168.1.100:30127 | Recherche Meilisearch + fichiers Range. |

Hôte SSH (`~/.ssh/config`) :

```
Host nasgul
  HostName 192.168.1.100
  User truenas_admin
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

Pilotage : `midclt call` (pas de `zfs` / `zpool` bruts). Sudo sans mot de passe encore actif pour la config — à retirer en Phase 2.

## Stockage

Disque boot 320 Go (`sde`) : ne jamais l’ajouter à un pool.

| Pool | Disques | Topologie | Chiffrement |
| --- | --- | --- | --- |
| `data` | 3 × ST2000VN004 (`sda` `sdb` `sdc`) | RAIDZ1, ~3,5 TiB | AES-256-GCM, clé HEX, déverrouillage auto |
| `backup` | 1 × WD20EFRX (`sdd`) | stripe 2 To | idem |

Datasets utiles :

- `data/nextcloud/html` — code Nextcloud (ACL apps uid 568, NFSv4)
- `data/nextcloud/data` — fichiers utilisateur, `recordsize` 1M
- `data/nextcloud/postgres_data` — `recordsize` 16K
- `data/shares` — partages futurs
- `data/media/button` — banque de sons BUTTON (`recordsize` 1M), SMB `BUTTON-Media`, Nextcloud **BUTTON Media**
- `data/ix-apps` — runtime Docker / apps TrueNAS
- `backup/data` — cible de réplication (dataset chiffré, **verrouillé** : normal pour un receive chiffré)

Les fichiers utilisateur restent sur RAIDZ1 même après l’arrivée du SSD (pool `apps` = Phase 2).

## Protection

Snapshots périodiques, récursifs sur `data`, y compris vides :

| Tâche | Schéma | Quand | Rétention |
| --- | --- | --- | --- |
| hourly | `auto-%Y-%m-%d_%H-%M-hourly` | chaque heure | 48 h |
| daily | `auto-%Y-%m-%d_%H-%M-daily` | 02:00 | 14 j |
| weekly | `auto-%Y-%m-%d_%H-%M-weekly` | dimanche 03:00 | 8 sem. |

Réplication `data-to-backup` : PUSH LOCAL, source `data` → `backup/data`, récursive, exclut `data/.system`.
TrueNAS 25.10 n’accepte pas `naming_schema` sur un PUSH, ni le mélange `name_regex` + tâches périodiques liées.
La tâche utilise `name_regex` `auto-.*` et un agenda 04:15.

Scrubs : dimanche 00:00, seuil 35 jours, pools `data` et `backup`.

SMART (pas d’UI dédiée en 25.10) via cron :

- court : dimanche 06:00 — `midclt call disk.smart_test SHORT '["*"]'`
- long : le 1er du mois 20:00 — `midclt call disk.smart_test LONG '["*"]'`

## Nextcloud (test)

Déployé sur le pool `data`, host-path vers les datasets ci-dessus, bind `0.0.0.0:30125` (l’API refuse un bind sur l’IP LAN seule).
Postgres 18, Redis, cron `*/5`, 2 Go RAM / 2 CPU. Inscription publique off, brute-force on.

Limites d’upload (après durcissement) : fichier 16 Go, PHP 1024 Mo / 3600 s, Apache `Timeout` 3600, `RequestReadTimeout` désactivé, jusqu’à 20 000 fichiers par requête, chunks WebDAV 100 Mo. Conf persistante : `/mnt/data/nextcloud/server-conf/`.
Le navigateur reste fragile pour un dossier énorme (milliers de fichiers / dizaines de Go) : client desktop Nextcloud, ou copie dans `data/admin/files` puis `occ files:scan admin`.

Vérifié le 17 sept. 2026 : login `admin`, fichier `nasgul-phase1-test.txt` visible dans Fichiers (WebDAV 201).

Identifiants : `~/.truenas-secrets/nextcloud-credentials.json`.

## Alertes e-mail — à faire dans l’UI

L’API ne peut pas terminer le flux Gmail OAuth. Session TrueNAS expirée : se reconnecter, puis :

1. Ouvrir https://192.168.1.100 (compte `truenas_admin`).
2. **Système → Paramètres généraux → Courrier** (ou **Alertes → Courrier**).
3. Méthode **Gmail OAuth**, autoriser le compte Gmail.
4. Nom d’expéditeur : `TrueNAS Nasgul`.
5. Adresse e-mail du compte `truenas_admin` (Credentials → Utilisateurs).
6. Envoyer un message de test, puis déclencher une alerte (ex. SMART) pour confirmer.

Tant que OAuth n’est pas fait, `mail.send` échouera.

## Restauration / secrets

Fichiers locaux (`chmod 600`, dossier `700`) :

- `~/.truenas-secrets/data.key.txt` / `data.keys.json`
- `~/.truenas-secrets/backup.key.txt` / `backup.keys.json`
- `~/.truenas-secrets/truenas-config.tar` (export avec *Password Secret Seed* + clés de pools, après Nextcloud)
- `~/.truenas-secrets/truenas-config-pre-nextcloud.tar` (export précédent)
- `~/.truenas-secrets/nextcloud-credentials.json`

Pour réimporter la config : UI **Système → Paramètres généraux → Gérer la configuration → Téléverser**, ou `config.upload` via `midclt`.

Pour lire la réplique : déverrouiller `backup/data` avec la clé du pool `data` (fichier, pas en argument de commande).

## Hors Phase 1

Pool SSD `apps`, Collabora, Imaginary, Tailscale / Cloudflare, sauvegarde hors site, 2FA Nextcloud, retrait du sudo NOPASSWD, arrêt SSH hors maintenance.
