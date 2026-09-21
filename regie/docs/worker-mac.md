# Worker de transcription sur le Mac

Le NAS n’a pas la puissance pour Whisper. Le Mac prend les jobs `shows.transcribe` par bail sur la même table `jobs`, transcrit avec `mlx-whisper`, écrit le JSON à côté du master, et rend la main. Régie continue sans lui ; les jobs attendent en file.

## Prérequis

- Le partage `BUTTON-Media` monté sur le Mac (`/Volumes/BUTTON-Media`).
- Postgres joignable via Tailscale : Nasgul expose le port 30131 sur son IP Tailscale (voir `compose.yml`).
- `pip install mlx-whisper` dans le même venv que Régie.

## Lancer

```bash
cd regie
export REGIE_DSN="postgresql://regie:MOT_DE_PASSE@nasgul.taild4714f.ts.net:30131/regie"   # POSTGRES_PASSWORD de secrets.env
export REGIE_SECRET="…"                      # même valeur que sur Nasgul (déchiffrement des secrets)
export REGIE_MEDIA_ROOT=/Volumes/BUTTON-Media
export REGIE_RUN_WORKERS=0
PYTHONPATH=src .venv/bin/python -m regie worker transcribe
```

Le worker affiche `{"worker": "worker:<mac>", "lanes": ["transcribe"]}` et boucle. Dans Régie, « Transcrire » sur un épisode met le job en file ; le worker le prend, `--word-timestamps True --condition-on-previous-text False`, prompt initial généré (nom de l’émission, invités reliés). Une seule passe fait foi : le JSON écrit devient la référence.

Pour le faire tourner en tâche de fond : un `launchd` `KeepAlive` ou simplement un onglet de terminal pendant la session de montage.

## Réglages

- `shows.transcribe_engine` : `mlx` (Mac), `faster` (NAS avec `faster-whisper`), vide = automatique.
- `shows.whisper_model` : `mlx-community/whisper-large-v3-turbo` par défaut.
