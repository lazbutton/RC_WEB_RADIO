# New Trad Radio — Radiotomate

Ce dossier fait partie du monorepo **RC_WEB_RADIO** (`github.com/lazbutton/RC_WEB_RADIO`).  
Ce n’est **plus** un clone lié aux remotes de Martin (sr.ht / Heptapod).

Code dérivé de [Radiotomate](https://radiotomate.org/) (Martin Kirchgessner), **AGPL-3.0-or-later**. Snapshot de base : miroir git `130030b` (v0.1.0), puis patches NTR. Le `README.md` de ce dossier reste celui de l’amont ; pour NTR, lire **ce fichier**.

Inventaire API / SASS / ports : [`docs/radiotomate-inventaire.md`](../docs/radiotomate-inventaire.md).

## Patches NTR (volontairement minces)

Ne pas réécrire `playout/radiotomate.liq`.

- `INTERFACE_NAME` : **New Trad Radio**
- `beets/compat.py` : ioctl TIOCGWINSZ 8 octets (Python 3.14 + Beets 2.5, sinon `interface --demo` plante à l’import)
- `layout.jinja` : `lang=fr`, logo, `ntr.css`
- `sass/_ntr.scss` + `static/ntr.css` : teinte `#6b2d1a`, pupitre sombre
- Pages Live / Carts / Auto-DJ / Mon compte : console producteur (ON AIR, skip, « Diffuser maintenant »)

## Démo sur ce Mac (UI seulement)

Pas de vrai playout ici (Liquidsoap + Podman + systemd = Linux). La démo sert à travailler l’UI et les carts.

```bash
export PATH="/opt/homebrew/bin:$PATH"   # poetry Homebrew
poetry install
poetry run radiotomate develop
poetry run radiotomate -c radio_data/radiotomate.yaml users add ntr --admin
poetry run radiotomate -c radio_data/radiotomate.yaml interface --demo --reload
```

- UI : http://127.0.0.1:6811  
- Compte démo `--demo` : `ntr` / `ntr-demo-change-me` (si `users add` a déjà tourné, utiliser ce compte)

Données locales (gitignorées) : `radio_data/` (SQLite, YAML, carts).

## Prod

Install sur la VM Linux, puis extraits [`ops/`](../ops/README.md). Recette : [`ops/runbook-labomedia.md`](../ops/runbook-labomedia.md).
