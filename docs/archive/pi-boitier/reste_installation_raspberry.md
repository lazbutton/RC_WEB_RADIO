# Reste à faire — boîtier Raspberry Pi 3B

> **Prod NTR (sept. 2026)** : l’automate n’est plus Liquidsoap sur ce Pi, c’est **Radiotomate** sur Labomedia. Voir [docs/README.md](docs/README.md). Ce fichier ne couvre que l’encodeur live optionnel (iD14 → port 6800) et l’état matériel du 3B.

Checklist d’exécution pour `rc-web-radio`, calée sur [projet_boitier_webradio_icecast_v3_pi3b.md](projet_boitier_webradio_icecast_v3_pi3b.md). Ce fichier ne remplacerait le cahier que pour le boîtier.

Dernière mise à jour : **15 sept. 2026, ~01:35 CEST**.

---

## 0. Instantané

| Point | Valeur |
|---|---|
| Hôte | `rc-web-radio` / `rc-web-radio.local` |
| SSH admin | `boitier-radio-admin` → user `laz` |
| SSH radio | `boitier-radio` → user `radio` |
| Matériel | Raspberry Pi 3 Model B Rev 1.2 |
| OS | Raspberry Pi OS Lite 64 bits, Debian 13 |
| Kernel | `6.18.39+rpt-rpi-v8` |
| Liquidsoap | **2.3.2**, service `radio.service` **active / enabled** |
| Carte son | Audient iD14 (`2708:0008`), ALSA `iD14` |
| Sortie | `output.dummy` (Icecast commenté) |

### Déjà en place

Système (§5) : paquets, pin FFmpeg Debian, CPU `performance`, firmware audio/BT/watchdog/`gpu_mem`, `usbcore.autosuspend=-1`, `nrpacks=1` (ignoré par ce noyau), journald volatile, fstab `commit=120`, user `radio`, arborescence `/srv/radio`.

Graphe radio :

- [`radio.liq`](radio.liq) → `/srv/radio/radio.liq`
- [`radio.env.example`](radio.env.example) → `/etc/radio/radio.env` (placeholder)
- [`deploy/radio.service`](deploy/radio.service) enabled, `TimeoutStartSec=180` (`--check` est lent sur le 3B)
- Healthcheck copié, **timer disabled** (pas d’Icecast)
- Médias de test générés (silence / sinus 48 kHz 128 kb/s)
- Clé Mac dans `~radio/.ssh/authorized_keys`
- `~/bin/radio-push` + `~/.ssh/config` (`boitier-radio`, `boitier-radio-admin`)

### Écarts volontaires par rapport au cahier

- Wi‑Fi conservé (pas de `disable-wifi`)
- zram laissé actif
- sshd non durci (`PasswordAuthentication` encore possible)
- Icecast commenté ; `output.dummy(id="horloge")` garde le graphe en vie
- `input.alsa` en **`plughw:CARD=iD14`** (12 canaux natifs → stéréo)
- `bufferize=` retiré (n’existe plus en Liquidsoap 2.3) ; `buffer()` reste
- Second `mksafe` après `crossfade` (sinon `limit` refuse une source fallible)
- Healthcheck Icecast **installé mais pas activé**

---

## 1. Bloqueurs matériels encore ouverts

### 1.1 Alimentation — `throttled=0x70005`

Sous-tension **en cours** (bits 0 et 2) + historique. L’iD14 est bus-powered sur le 3B : trop tiré sur l’USB.

- Alim officielle **5,1 V / 2,5 A**
- iD14 sur **son propre secteur**
- Recette : cycle **secteur**, puis `vcgencmd get_throttled` → `0x0`

Sans ça : catchup d’horloge dans les logs, xruns possibles, crashs.

### 1.2 Dissipateur — ~76–80 °C

Toujours trop chaud. Dissipateur + aération. Throttle à 80 °C.

### 1.3 Carte son — faite (logiciel)

| Champ | Valeur |
|---|---|
| USB | `2708:0008` Audient iD14, UAC (class Audio) |
| Nom ALSA | `iD14` → `plughw:CARD=iD14,DEV=0` |
| Capture | 12 ch, S32_LE 24 bit, **44100 / 48000 / 88200 / 96000** |
| Graphe | 48 kHz stéréo |
| Test | `arecord -D plughw:CARD=iD14,DEV=0 -f S16_LE -c 2 -r 48000` OK |

`hw:CARD=iD14` brut est inutilisable en stéréo (12 canaux). Ne pas revenir à un index `hw:0,0`.

---

## 2. Checklist Pi

### 2.1 Carte son (§6)

- [x] Détectée, `plughw` testé, samplerate 48 kHz dans `radio.liq`

### 2.2 Médias minimum (§7)

- [x] `secours/boucle.mp3` (30 s silence, placeholder)
- [x] `musique/Test - Souffle.mp3`, `nuit/` copie, `jingles/Jingle - Test.mp3`
- [ ] Vrais titres studio + tags ID3 + ReplayGain (`loudgain` sur le Mac)
- [ ] `emissions/` encore vide

### 2.3 Script `radio.liq` (§8)

- [x] Déployé, `--check` OK, live iD14 câblé
- [ ] Décommenter `output.icecast` quand le VPS existe

### 2.4 Secrets Icecast (§10.1)

- [x] `/etc/radio/radio.env` placeholder (`radio.exemple.org`)
- [ ] Vrais `ICECAST_HOST` / mot de passe source / URL

### 2.5 Test manuel (§15)

- [x] Service systemd à la place (le `--check` manuel est trop long pour un `timeout` confortable)
- [x] Socket `/run/liquidsoap/radio.sock`, `now.json` écrit

### 2.6 Service systemd (§10.2)

- [x] `radio.service` enabled + active
- Suivre : `journalctl -u radio -f` / `ssh boitier-radio-admin`

### 2.7 Healthcheck (§10.3)

- [x] Fichiers posés (`/usr/local/bin/radio-healthcheck` + unit + timer)
- [ ] `systemctl enable --now radio-healthcheck.timer` **après** Icecast réel (le script no-op si `ICECAST_HOST=radio.exemple.org`)

### 2.8 Envoi playlists Mac (§9)

- [x] Clé `radio`, Host `boitier-radio`, [`scripts/radio-push`](scripts/radio-push) → `~/bin/radio-push`
- [ ] Créer `~/Radio/media/` et lancer `radio-push` (loudgain optionnel)
- Pas de `--delete` par défaut (sécurité)

### 2.9 Tailscale (§5.6)

- [ ] Non fait (login interactif). Puis sshd : `PasswordAuthentication no`, `AllowUsers laz radio`

### 2.10 NUT (§5.5)

- [ ] Quand l’onduleur USB est branché

### 2.11 Ne pas faire maintenant

OTP USB boot, `disable-wifi`, tuer zram, archivage, second flux Opus.

---

## 3. Hors Pi — toujours bloquant pour l’antenne publique

Icecast + `fallback-mount` `/secours.mp3` + TLS. Ensuite : remplir `radio.env`, décommenter `output.icecast` dans [`radio.liq`](radio.liq), `liquidsoap --check`, `systemctl restart radio`, activer le timer healthcheck.

---

## 4. Recette

### V1 — pas encore

Il manque alim saine (`0x0`), iD14 auto-alimenté, vrais médias, Icecast. Le graphe **local** tourne (`output.dummy` + playlist nuit/musique + live ALSA en buffer).

### V2 — après Icecast + NUT + healthcheck

---

## 5. Prochaine action

1. Alim 2,5 A + alimentation secteur de l’iD14 + dissipateur.
2. Pousser de vrais MP3 via `radio-push` (dossier `~/Radio/media/`).
3. VPS Icecast → `radio.env` → décommenter la sortie → activer `radio-healthcheck.timer`.
4. Tailscale, puis durcir sshd. NUT avec l’onduleur.
