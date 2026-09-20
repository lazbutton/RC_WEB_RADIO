# BUTTON — Radiotomate

Ce dossier fait partie du monorepo **RC_WEB_RADIO**. 

Code dérivé de [Radiotomate](https://radiotomate.org/) (Martin Kirchgessner), **AGPL-3.0-or-later**. Le `README.md` de ce dossier reste celui de l’amont.

Libellés UI : [`button/brand.json`](../button/brand.json) (`INTERFACE_NAME` au boot). Inventaire : [`docs/radiotomate-inventaire.md`](../docs/radiotomate-inventaire.md).

## Patches (volontairement minces)

Ne pas réécrire `playout/radiotomate.liq`.

- `INTERFACE_NAME` / login : brand `labels.automate`
- `beets/compat.py` : ioctl TIOCGWINSZ 8 octets (Python 3.14 + Beets 2.5)
- `layout.jinja` : `lang=fr`, logo, `suite.css`
- `sass/_suite.scss` + `static/suite.css` : charte N&B, pupitre sombre

## Démo sur ce Mac (UI seulement)

```bash
export PATH="/opt/homebrew/bin:$PATH"
poetry install
poetry run radiotomate develop
poetry run radiotomate -c radio_data/radiotomate.yaml users add lazbutton --admin
poetry run radiotomate -c radio_data/radiotomate.yaml interface --demo --reload
```

- UI HTMX : http://127.0.0.1:6811
- Console admin : repo `button-console` (http://127.0.0.1:5175/)

Données locales (gitignorées) : `radio_data/`.

Infra : [button-ops](https://github.com/lazbutton/button-ops).
