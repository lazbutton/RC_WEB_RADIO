# Archive — boîtier Pi 3B (automate local)

Cahier et fichiers d’un projet **abandonné** : Liquidsoap 24/24 sur Raspberry Pi 3B (`/srv/radio`), Icecast distant, healthcheck systemd.

La prod NTR est Radiotomate sur la VM Labomedia. Ne pas installer ce graphe pour New Trad Radio.

| Fichier | Ancien rôle |
|---|---|
| `projet_boitier_webradio_icecast_v3_pi3b.md` | Cahier (capture iD14, playlists, Icecast) |
| `reste_installation_raspberry.md` | Checklist d’install Pi |
| `radio.liq` | Graphe Liquidsoap (sortie `output.dummy`) |
| `radio.env.example` | Secrets Icecast |
| `deploy/` | Units systemd `radio` + healthcheck |
| `scripts/radio-push` | `scp` du `.liq` vers le boîtier |

Si on réutilise le Pi comme **encodeur QG** (BUTT / ALSA → harbor Radiotomate `:6800`), voir [`../../live-qg.md`](../../live-qg.md) — pas ce `radio.liq` playlist.
