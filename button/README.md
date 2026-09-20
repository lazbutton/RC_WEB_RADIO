# BUTTON — config marque blanche

Un fichier, [`brand.json`](brand.json), porte l’identité, les libellés UI, les URLs, l’infra et la charte. **Pas de mots de passe.**

Changer la marque = éditer ce JSON, pas un grep dans les apps.

## Charte

Tokens : [`theme.css`](theme.css) + `theme` dans `brand.json`. Fond sombre, **uniquement noir / blanc / gris**.

Couleur autorisée ailleurs :

1. Player de la **console** (onde, VU, pastilles autodj / ON AIR dans le topbar).
2. Retours opérationnels : ON AIR / live (`#ff4d4d`), OK / autodj (`#3ecf8e`), VU / clip, erreur.

Le player public, hub, pads, Inbox Zero et l’overlay Radiotomate restent N&B. Les types éditoriaux (musique, jingle, pub…) se distinguent par densité, pas par teinte.

Logos : carré `#0a0a0a`, glyphe `#f5f5f5`.

## Contrat

- Titres, `h1`, aria-labels, toasts, `CFBundleName` : `labels.*` (+ interpolation `{name}`, `{short}`, `{share}`).
- URLs : `apps.*`. Share SMB / dataset : `infra.*`.
- Couleurs : `theme.*`.
- Secrets : `~/.truenas-secrets/` et `secrets/LANCEMENT.md` (hors git).

## Chargeurs

- Python : [`brand.py`](brand.py) (`load_brand()`, `label()`). `BUTTON_BRAND` = chemin JSON. Les paquets embarquent une copie.
- JS : [`loadBrand.ts`](loadBrand.ts). `VITE_BUTTON_HUB_URL` pour fetch le hub, sinon `./brand.json`.

## Hub

Page d’accueil de la suite, port **30120**. Sert `brand.json` et des tuiles vers les apps.

```bash
cd button/hub
python3 -m http.server 30120
# ou : ./button/hub/deploy.sh  (Nasgul)
```
