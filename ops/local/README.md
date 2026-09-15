# Banc local (macOS) — Icecast + Now Playing

Le script officiel Radiotomate exige Linux + Podman + systemd user. Ici on n’allume que le **tuyau audio de test**.

Recette pas à pas : [`../banc-mac.md`](../banc-mac.md).

```bash
cd ops/local
docker compose up --build -d
./generate-test-media.sh   # une fois
```

| Service | URL |
|---|---|
| Icecast | http://127.0.0.1:18000/ — admin `admin` / `ntradmin`, source `ntrhackme` |
| Now Playing | http://127.0.0.1:6820/now.json |
| Hook métadonnées | `POST /hook` Bearer `ntr-dev-secret` |

Icecast dans le conteneur : port **8000**. Côté Mac : **18000** (`18000:8000`).

Médias de test : `data/MusicDropbox/{rotation,ntf1,ntf2,ntf3,habits}` (gitignoré).

Streamer cart (ID3 artiste/titre) : `python3 stream-cart.py`.

Player : [`player/`](../../player/README.md) (`npm run dev` → http://127.0.0.1:5174/). Sans source Icecast, le player est muet. L’oscilloscope exige les CORS Icecast (rebuild de ce Compose si `icecast.xml` change).

UI Radiotomate démo : [`radiotomate/NTR.md`](../../radiotomate/NTR.md).
