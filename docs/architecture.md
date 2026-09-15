# Architecture New Trad Radio

Quatre dossiers à la racine : [`player/`](../player/README.md), [`festival/`](../festival/README.md), [`radiotomate/`](../radiotomate/NTR.md), [`ops/`](../ops/README.md). Carte : [`README.md`](../README.md).

Priorité d’antenne Radiotomate : **live > relais > carts > auto-DJ**.

```text
Producteurs (RCO, Zef, P-Node, Zamzamrec)
        │  UI admin (Radiotomate, branche ntr/theme)
        ▼
   Scheduler :6822 (JSON, token interne)
        │
        ├── Playout Liquidsoap ──► Icecast Labomedia (:8443, mount NTR)
        │                              │
        │                              ├── player/ (site auditeur)
        │                              └── relais partenaires
        └── metadata_log.relay_to ──► ops/nowplaying (now.json)

QG St Aignan (Pi 3B ou laptop, BUTT)
        └── harbor :6800 /stream  (rôle stream)
```

## Hôte

VM Linux dédiée Labomedia (Debian/Ubuntu), Podman + quadlets. Images ~2 Go. Médias 100 Go–1 To.

Le Pi 3B (1 Go, bus USB saturé, sous-tension) n’héberge pas Radiotomate.

## Ports

| Port | Service |
|---|---|
| 6811 | Interface web producteurs |
| 6800 | Entrée live (Icecast-like) |
| 6822 | Scheduler (interne au pod) |
| 6833 | API playout (interne) |
| 8443 | Icecast public Labomedia (existant) |

Caddy (ou nginx Labomedia) termine TLS vers 6811 et 6800. Ne pas exposer 6822/6833.

## Icecast depuis un conteneur

`localhost` / `127.0.0.1` ne marchent pas. Même machine : `host.containers.internal`. Sinon hostname Icecast Labomedia.

## Licence

AGPL-3.0-or-later. Instance publique = dépôt source public (ce repo).
