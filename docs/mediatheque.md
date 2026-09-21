# Médiathèque BUTTON (Nasgul)

Source de vérité : dataset ZFS **`data/media/button`** monté en `/mnt/data/media/button`.  
Ce n’est **pas** le dossier Nextcloud utilisateur, et Beets **ne déplace plus** les fichiers.

## Arbre

| Dossier | Rôle |
|---|---|
| `00-inbox/rotation/` | Dépôt musique auto-DJ (tags artiste + titre obligatoires) |
| `00-inbox/archives/ntf1\|ntf2\|ntf3/` | Dépôt archives |
| `00-inbox/habillage/{jingles,ids,pubs,beds,stings,sfx}/` | Dépôt habillage |
| `00-inbox/emissions/` | Dépôt émissions |
| `10-rotation/` | Auto-DJ (`grouping=rotation`) — **à l’antenne** |
| `20-archives/ntf1\|ntf2\|ntf3/` | Archives NTF |
| `30-habillage/…` | Jingles, IDs, pubs, beds, stings, SFX — carts + Pads |
| `40-emissions/{nom}/` | Émissions |
| `50-carts/{id}/` | Dossier réel des carts (Prog, customs) |
| `Carts/{id}-{titre}/` | Vue Finder (liens vers habillage ou `50-carts`) |
| `90-trash/` | Corbeille, jamais à l’antenne |

`00-inbox/` n’est jamais indexé pour la diffusion. Le catalogue **promeut** un fichier dès qu’il est valide vers `10` / `20` / `30`.

## Noms de fichiers

- Musique : `Artiste - Titre.mp3` + ID3 artiste et titre
- Habillage : `kind_emission_descriptif.wav` (ex. `jingle_button_ouverture.wav`)

## Accès

| Voie | URL / chemin |
|---|---|
| Finder SMB (studio) | `smb://192.168.1.100/BUTTON-Media` (compte `laz`) — carts dans `Carts/` |
| Finder SMB (Tailscale) | `smb://nasgul.taild4714f.ts.net/BUTTON-Media` si le LAN n’est pas joignable |
| Nextcloud | dossier **BUTTON Media** (stockage externe Local) |
| Catalogue | http://192.168.1.100:30127/search?q= |
| Santé catalogue | http://192.168.1.100:30127/health |

Gros dumps : SMB, pas le navigateur Nextcloud.

ACL NFS4 sur le dataset : `laz` (3000), groupe `buttonmedia` (3001), `rtuser` (1000), Nextcloud apps (568) **et** `www-data` du conteneur Nextcloud (**uid 33** — sans ça, `occ files:scan` refuse `/media/button`).

## Dossiers verrouillés (Finder)

Compte SMB `laz` : **fichiers** = drop et corbeille ; **dossiers** = lecture seulement (pas de mkdir, rename, déplacement, suppression). Ça évite de casser `Carts/{id}-{titre}` et le reste de l’arbre.

Les dossiers se créent encore par Radiotomate (`rtuser` 1000), le catalogue, `setup-media-tree.sh`, et Nextcloud (uid 33 — le deny ne le concerne pas). Un thème `10-rotation/jazz` se fait par le catalogue / l’arbre, pas depuis le Finder.

Script Nasgul (root, idempotent) : `scripts/lock-media-folders-acl.sh` dans button-ops, sur `/mnt/data/media/button`. Dataset : `aclmode=passthrough`, `aclinherit=passthrough`. Un rename de dossier dans le Finder affiche une erreur macOS : c’est voulu.

## Consommateurs

- **Radiotomate** : volume `/media`. Carts : le dossier Finder `Carts/{id}-{titre}/` **est** le cart (`50-carts/{id}` ou `30-habillage/…`). Un drop Finder est attaché par `bank_sync` (~12 s). Un upload console écrit dans ce dossier. `Sound.path` reste le chemin canonique `media:…` (jamais le lien `Carts/`). Supprimer un son exclusif le déplace vers `90-trash/`.
- **Pads** : bibliothèque réseau (`VITE_CATALOG_URL`), cache IndexedDB seulement des pads mappés. Import local toujours possible si le NAS est down.
- **Nextcloud** : même arbre, en lecture/écriture équipe.

## Dépôt musique

1. Copier le MP3 taggé dans `00-inbox/rotation/`
2. Le catalogue le déplace vers `10-rotation/`
3. Le sidecar Beets l’indexe (`grouping=rotation`)
