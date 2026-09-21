# rco-site — bord public des blocs Radio Campus Orléans

Ce dossier est la cible statique publiée par Régie (module Publier). Il ne contient aucune logique : Régie génère et pousse les fichiers, le site WordPress les intègre en iframes.

## Contenu généré (par Régie, jamais à la main)

| Chemin | Rôle |
| --- | --- |
| `agenda/index.html` + `data.json` | Agenda (Outlive : événements Radio Campus ou couverts) |
| `upcoming/index.html` + `data.json` | Prochainement à l’antenne (grille + événements Radio Campus) |
| `podcasts/index.html` + `data.json` | Derniers podcasts publiés |
| `playlist/index.html` + `data.json` | Playlist de la semaine |
| `team/index.html` + `data.json` | L’équipe (personnes taguées `equipe`) |
| `rss/<emission>.xml` | Flux RSS podcast par émission |
| `index.json` | Manifeste (date, blocs, flux) |

## Contrat embed

Chaque bloc est un HTML autonome (CSS et JS inclus, CSP `default-src 'none'`). Dans WordPress :

```html
<iframe src="https://blocs.radiocampus.org/podcasts/index.html?embed=1" id="regie-podcasts" style="width:100%;border:0;height:480px" loading="lazy"></iframe>
<script>window.addEventListener('message',e=>{if(e.data&&e.data.type==='regie:height'&&e.data.block==='podcasts')document.getElementById('regie-podcasts').style.height=e.data.height+'px'});</script>
```

- `?embed=1` masque le titre et les marges.
- Le bloc envoie `{type:'regie:height', block, height}` à chaque changement de taille ; il écoute `{type:'regie:scroll', top}`.
- Les liens s’ouvrent dans la page parente (`target=_top`).

Les extraits prêts à coller sont dans Régie → Publier.

## Hébergement

- **Vercel** (déjà utilisé pour l’agenda historique `radio-campus.vercel.app`) : `EDGE_PROVIDER=vercel`, `EDGE_TOKEN`, `EDGE_PROJECT`. `vercel.json` ci-contre fixe les en-têtes.
- **Cloudflare Pages** : `EDGE_PROVIDER=cloudflare`, `EDGE_TOKEN`, `EDGE_PROJECT`, `EDGE_ACCOUNT_ID` ; `_headers` ci-contre.
- La perte du bord se répare par « Régénérer le bord public » dans Régie.

L’ancien projet Vite/React de l’agenda iframe (identité Labomedia, deux flux Outlive fusionnés) a vocation à être rapatrié ici et remplacé par le bloc `agenda` généré.
