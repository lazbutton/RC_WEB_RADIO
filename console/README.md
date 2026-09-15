# Console automate — proto DA

Squelette **Vite + React + Radix Themes** : pupitre live-assist + atelier d’horloges ([`docs/automate-horloges.md`](../docs/automate-horloges.md)).

Ce n’est **pas** la console producteurs en production. L’interface réelle reste Radiotomate Quart/HTMX sur **`:6811`**. Catalogue **mock en mémoire** (métadonnées, pas de fichiers, pas d’API). ADR-H7 : dossier de design, pas un remplacement du fork.

## Lancer

```bash
cd console
npm install
npm run dev
# http://127.0.0.1:5175/  → /antenne  (port 5175 ; le player public est sur 5174)
```

Build : `npm run build`.

## Routes

| Route | Intention |
|---|---|
| `/antenne` | Pupitre : Now/Next, skip, pads sons/jingles/pubs (même file) |
| `/horloges` | Horloges Journée / Soir, réglette 00–60, motif réel |
| `/semaine` | Grille 7 × 24 h, dayparts Journée 7h–19h / Soir |
| `/conducteur` | Même file que le pupitre, ~30 min |
| `/categories` | Rotation / Archives + requêtes Beets |
| `/habillage` | Carts Jingles NTR, Pubs, Promos |

`/` redirige vers `/antenne`. Pas de harbor QG. Refresh = reset du mock. Skip / pads mettent à jour Now partout.

## Hors scope

Persistance disque, branchement Quart, auth, player, festival, ops, MIDI. DnD file / conducteur : déjà dans le proto.
