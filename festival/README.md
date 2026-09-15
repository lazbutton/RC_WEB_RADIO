# Grille festival — NTF#4

Dashboard de programmation (5–9 mai 2027, Saint-Aignan).

Radiotomate ne publie **pas** de playlist à l’avance (live > relais > carts > auto-DJ, décidé à l’antenne). Ce dossier est la vue éditoriale pour l’équipe : qui passe quand, en face du Now Playing.

Ce n’est pas l’automate. Changer la grille ici ne change rien au playout.

## Fichiers

| Fichier | Rôle |
|---|---|
| `index.html` | Page |
| `grille.js` | Créneaux par jour (`window.NTR_FESTIVAL`) + URL `now.json` |
| `app.js` | Rendu des colonnes + poll Now Playing |
| `style.css` | DA (même famille que le player) |
| `logo-ntr.svg` | Marque |

## Lancer

Depuis la **racine du dépôt** :

```bash
python3 -m http.server 8765
# http://127.0.0.1:8765/festival/
```

Le player immersif tourne à part : `cd player && npm run dev` → http://127.0.0.1:5174/

Adapter `grille.js` quand la programmation janvier–avril 2027 est figée. Le bandeau « à l’antenne » lit le même `now.json` que le player (`ops/local` en banc).
