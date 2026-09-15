# Comptes, rôles, médiathèque

Radiotomate suppose que les comptes sont des membres de confiance ([doc rôles](https://radiotomate.org/users.html)). Pas de droits fins type « un upload par semaine ».

## Comptes à créer (2 admins minimum)

| Compte | Structure | Rôles |
|---|---|---|
| `admin-ntr` | NTR / Labomedia | `admin` |
| `olmo` | Zamzamrec / flux P-Node | `admin` (2e admin) |
| `heloise` | Zamzamrec | `carts`, `autodj` |
| `viviane` | Radio Campus Orléans | `carts`, `autodj`, `live` |
| `tim` | Radio Campus Orléans | `carts`, `autodj`, `live`, `stream` |
| `zef` | Studio Zef | `carts`, `stream`, `live` |
| `pnode` | P-Node | `carts`, `stream`, `live` |
| `qg-st-aignan` | Encodeur festival | `stream` uniquement |

CLI (dans le pod) :

```bash
radiotomate --config-path "$RTCONFIG" users add admin-ntr --admin
radiotomate --config-path "$RTCONFIG" users add tim --role carts --role autodj --role live --role stream
radiotomate --config-path "$RTCONFIG" users roles   # liste des identifiants
```

Mots de passe : phrases longues (`minimum_password_length: 20`). Pas d’e-mail de reset : un admin réinitialise.

## Taxonomie MusicDropbox

Dossiers créés par `ops/scripts/setup-dropbox.sh`. `drop2beets` déplace vers `Music/` après tags Artist + Title.

| Dossier dropbox | Tag Beets (on_item) | Usage auto-DJ |
|---|---|---|
| `ntf1/` | `grouping=ntf1` `genre=Archive` | Archives édition 1 |
| `ntf2/` | `grouping=ntf2` | Archives édition 2 |
| `ntf3/` | `grouping=ntf3` | Archives édition 3 |
| `rotation/` | `grouping=rotation` | Auto-DJ 24/24 |
| `habits/` | — | Habillage (plutôt **carts jingles**, pas Beets) |

Filtres créneaux (syntaxe Beets) :

- Archives jour : `grouping:ntf1,ntf2,ntf3`
- Soir rotation : `grouping:rotation`
- Récent : `added:-3m..`

## Carts à créer dans l’UI

1. **Jingles NTR** — mode randomized playlist, associé à tous les créneaux auto-DJ.
2. **Archives NTF#1 / #2 / #3** — playlist, programmés hors festival.
3. **Émissions partenaires** — un cart par radio (RCO, Zef, P-Node).
4. **Relais P-Node / Zef / RCO** — cart URL Icecast, durée max, soirées simultané (ex. 13–14 nov. 2026).

Jingles : au moins un fichier **avant** d’attendre de la musique. Recette install officielle.

Convention fichiers : `Artiste - Titre.mp3` + ID3. ReplayGain calculé en fond (Beets) ; ne pas coller un fichier dans un cart qui part 3 secondes plus tard.
