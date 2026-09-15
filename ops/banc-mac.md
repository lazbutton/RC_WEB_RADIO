# Banc Mac — tout lancer

Deux terrains : **ce Mac (banc)** et **Labomedia (prod)**. Radiotomate complet (auto-DJ, carts vers l’antenne, live `:6800`) ne tourne **pas** sur macOS : Podman + systemd user, Linux seulement.

Racine du dépôt : dossier parent de `ops/` (`RC_WEB_RADIO`).

## Ce qui tourne où

| Chose | Local (Mac) | Prod |
|---|---|---|
| Icecast de test | Docker `:18000` | Labomedia `:8443` |
| Titre en cours | nowplaying `:6820` | même sidecar ou `relay_to` |
| Player | `player/` (`npm run dev` → `:5174`) | site derrière Caddy (`dist/`) |
| Grille festival | `festival/` | idem |
| Radiotomate UI `:6811` | `interface --demo` (sans playout) | `install.sh` + systemd |
| Pi 3B | optionnel, encodeur live | QG mai 2027 |

Icecast de test : dans le conteneur le daemon écoute **8000**, exposé sur l’hôte en **18000** (`18000:8000` dans le Compose).

## 1. Icecast + Now Playing

Ouvrir Docker Desktop (moteur vert), puis :

```bash
cd ops/local
docker compose up --build -d
docker compose ps
```

- Icecast admin : http://127.0.0.1:18000/ — `admin` / `ntradmin` (source `ntrhackme`)
- Now Playing : http://127.0.0.1:6820/now.json

Si le port **6820** est pris : `lsof -nP -iTCP:6820` puis tuer l’ancien `nowplaying.py`.

Médias de test (une fois) :

```bash
cd ops/local
./generate-test-media.sh
```

Fichiers dans `ops/local/data/MusicDropbox/{rotation,ntf1,ntf2,ntf3,habits}`. L’auto-DJ Radiotomate ne les lit pas tant que Radiotomate n’est pas sous Linux.

## 2. Du son dans Icecast (sinon le player est muet)

Les MP3 des carts ont souvent une pochette en flux vidéo : **toujours** `-vn -map 0:a:0`.

```bash
ffmpeg -re -f lavfi -i sine=frequency=440:sample_rate=48000:duration=30 \
  -c:a libmp3lame -b:a 128k -content_type audio/mpeg \
  -ice_name "New Trad Radio" \
  -f mp3 "icecast://source:ntrhackme@127.0.0.1:18000/ntradio.mp3"
```

Flux : http://127.0.0.1:18000/ntradio.mp3

Boucle du cart « Prog 100% féminine » (tags ID3 artiste/titre, pas le nom de playlist) :

```bash
python3 ops/local/stream-cart.py
```

Pousser un titre vers le player :

```bash
curl -sS -X POST http://127.0.0.1:6820/hook \
  -H "Authorization: Bearer ntr-dev-secret" \
  -H "Content-Type: application/json" \
  -d '{"artist":"NTR","title":"Banc local","source":"test"}'
```

## 3. Player + grille

```bash
cd player && npm install && npm run dev
# http://127.0.0.1:5174/
```

Grille (statique) depuis la racine du dépôt :

```bash
python3 -m http.server 8765
# http://127.0.0.1:8765/festival/
```

## 4. Interface Radiotomate (démo)

`poetry` : Homebrew, puis **nouveau** terminal si `command not found`.

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd radiotomate
git checkout ntr/theme
poetry install
poetry run radiotomate develop
poetry run radiotomate -c radio_data/radiotomate.yaml users add ntr --admin
poetry run radiotomate -c radio_data/radiotomate.yaml interface --demo --reload
```

UI : http://127.0.0.1:6811 — admin **sans** vrai playout. Détail : [`radiotomate/NTR.md`](../radiotomate/NTR.md).

## 5. Tout éteindre

```bash
cd ops/local && docker compose down
```

Arrêter aussi `ffmpeg` et le `npm run dev` du player.

## 6. Si ça ne part pas

| Symptôme | Cause | Quoi faire |
|---|---|---|
| `docker compose` refuse | Docker éteint | Ouvrir Docker Desktop |
| Bind 18000 | autre Icecast / process | `lsof -nP -iTCP:18000` puis changer le bind Compose, ou libérer le port |
| Bind 6820 | `nowplaying.py` déjà lancé | `lsof` puis tuer le process |
| Player mute | Pas de source Icecast | Relancer le `ffmpeg` §2 |
| Oscilloscope plat | CORS Icecast / MSE | Rebuild Compose (`icecast.xml`), ou `streamUrl` same-origin |
| Icecast 404 / pas de mount | ffmpeg a envoyé la pochette MJPEG | `-vn -map 0:a:0` |
| `install.sh` sur Mac | Pas de systemd user | VM Linux / Labomedia |

Prod : [`runbook-labomedia.md`](runbook-labomedia.md). Live QG : [`docs/live-qg.md`](../docs/live-qg.md).
