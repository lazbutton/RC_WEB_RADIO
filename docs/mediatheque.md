# Médiathèque NTR (Nasgul)

Source de vérité : dataset ZFS **`data/media/ntr`** monté en `/mnt/data/media/ntr`.  
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
| `90-trash/` | Corbeille, jamais à l’antenne |

`00-inbox/` n’est jamais indexé pour la diffusion. Le catalogue **promeut** un fichier dès qu’il est valide vers `10` / `20` / `30`.

## Noms de fichiers

- Musique : `Artiste - Titre.mp3` + ID3 artiste et titre
- Habillage : `kind_emission_descriptif.wav` (ex. `jingle_ntr_ouverture.wav`)

## Accès

| Voie | URL / chemin |
|---|---|
| Finder SMB | `smb://192.168.1.100/NTR-Media` (compte `laz`) |
| Nextcloud | dossier **NTR Media** (stockage externe Local) |
| Catalogue | http://192.168.1.100:30127/search?q= |
| Santé catalogue | http://192.168.1.100:30127/health |

Gros dumps : SMB, pas le navigateur Nextcloud.

ACL NFS4 sur le dataset : `laz` (3000), groupe `ntrmedia` (3001), `rtuser` (1000), Nextcloud apps (568) **et** `www-data` du conteneur Nextcloud (**uid 33** — sans ça, `occ files:scan` refuse `/media/ntr`).

## Consommateurs

- **Radiotomate** : volume `/media`, Beets `import.move: no`, `beet import -A` sur `10-rotation` et `20-archives`. Carts : `POST /carts/:id/sounds.json` avec `{"paths":["30-habillage/jingles/…"]}` — le fichier n’est pas recopié ; le supprimer du cart ne l’efface pas de la banque.
- **Pads** : bibliothèque réseau (`VITE_CATALOG_URL`), cache IndexedDB seulement des pads mappés. Import local toujours possible si le NAS est down.
- **Nextcloud** : même arbre, en lecture/écriture équipe.

## Dépôt musique

1. Copier le MP3 taggé dans `00-inbox/rotation/`
2. Le catalogue le déplace vers `10-rotation/`
3. Le sidecar Beets l’indexe (`grouping=rotation`)
