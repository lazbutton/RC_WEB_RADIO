# Boîtier d'émission webradio — Raspberry Pi 3B + Liquidsoap + Icecast

Version 3 du cahier de projet, calibrée pour un hôte Raspberry Pi 3B. Objectif : une radio qui tourne 24/7 sans intervention, alimentée par des playlists poussées en SSH, avec un automate open source et un budget matériel proche de zéro.

---

## 0. Décisions prises

| Point | Décision |
|---|---|
| Hôte | Raspberry Pi 3B déjà disponible, carte SD dédiée |
| Automate | Liquidsoap seul (grille horaire, rotation, jingles, file prioritaire) |
| Système | Raspberry Pi OS Lite **64 bits** |
| Envoi des contenus | `rsync` sur SSH avec limitation de débit, rechargement par inotify |
| Flux V1 | un seul, MP3 128 kb/s, fréquence alignée sur la carte son |
| Secours | double filet, local et côté serveur (`fallback-mount`) |
| Serveur | Icecast sur VPS, derrière un reverse proxy HTTPS |
| Archivage | reporté après passage sur clé/SSD USB |

Deux points enterrés volontairement. L'écran OLED n'apporte rien qu'un `ssh` ne donne mieux. L'interface web n'est pas sur le Pi : Icecast pour l'écoute, Radiotomate sur Labomedia pour la console (prod BUTTON actuelle).

---

## 1. Contraintes

- Carte son USB déjà disponible.
- Pilotage à distance uniquement en SSH, pas d'écran ni de clavier sur le boîtier.
- Programmation musicale envoyée depuis le poste de travail.
- Diffusion continue, y compris studio vide.
- Le Pi 3B sert déjà à autre chose : le projet vit sur sa propre carte SD.

---

## 2. Choix de l'automate

| Solution | RAM mini | Interface | Envoi SSH | Verdict |
|---|---|---|---|---|
| **Liquidsoap seul** | ~200 Mo | script + socket de commande | natif (`rsync` + dossiers) | **retenu** |
| LibreTime | 2 Go | web, planning graphique | non | Postgres + RabbitMQ, maintenance ingrate |
| Rivendell | 2 Go | orientée station FM | non | surdimensionné |
| MPD + Liquidsoap | ~250 Mo | protocole MPD | oui | une brique de plus sans gain |

Liquidsoap n'est pas seulement un encodeur. Il porte la grille horaire, la rotation, les jingles, la détection de silence, le basculement live/auto et la file d'attente prioritaire. Tout tient dans un fichier texte versionnable en git, ce qui colle exactement à un pilotage SSH.

---

## 3. Architecture

```text
 Poste de travail
      │  rsync/ssh (fichiers audio + grille)
      ▼
┌──────────────────────────────┐
│  BOÎTIER — Raspberry Pi 3B   │
│                              │
│  Carte son USB ──► ALSA      │   ◄── table de mixage studio (live)
│                     │        │
│              ┌──────▼──────┐ │
│              │ Liquidsoap  │ │  grille + rotation + secours
│              └──────┬──────┘ │
│                     │        │
│              MP3 128 kb/s    │
└─────────────────────┼────────┘
                      │ Internet (Tailscale pour l'admin)
                      ▼
              ┌───────────────┐
              │ VPS           │
              │  Icecast      │  /radio.mp3
              │   └ fallback  │  /secours.mp3 (jamais coupé)
              │  Caddy (TLS)  │  https://radio.exemple.org/radio.mp3
              └───────┬───────┘
                      ▼
                  Auditeurs
```

---

## 4. Le Pi 3B comme hôte

### 4.1 Ce qui passe sans problème

Liquidsoap en MP3 128 kb/s consomme environ 10 % d'un cœur Cortex-A53. Le processus occupe 200 Mo, sur 1 Go disponible. Le facteur limitant n'est ni le CPU ni la RAM.

### 4.2 Les trois vraies contraintes

**Le bus USB est partagé avec l'Ethernet.** Sur le 3B, les quatre ports USB et le contrôleur réseau passent par le même LAN9514 en USB 2.0. Un transfert de fichiers pendant que la carte son capture produit des xruns audibles à l'antenne. Traité en §9.1 par une limitation de débit.

**La microSD ne survit pas aux écritures continues.** Journalisation en RAM obligatoire, archivage désactivé en V1, swap désactivé.

**L'alimentation est sous-dimensionnée par défaut.** Le 3B ne délivre que 1,2 A cumulés sur ses quatre ports USB. Une carte son bus-powered plus une alim médiocre donne exactement les symptômes d'un bug logiciel.

### 4.3 Carte SD dédiée

Le Pi 3B tourne déjà sous un autre système. Ne pas y toucher : une microSD neuve à 8 €, une image propre, et l'échange se fait selon l'usage. Le boîtier radio monopolise la carte son en permanence, aucune cohabitation en simultané n'est possible.

### 4.4 Pourquoi 64 bits

Contre-intuitif avec 1 Go, mais c'est le bon choix : les paquets `.deb` de Savonet sont publiés en amd64 et arm64, jamais en armhf. En 32 bits, il reste la version des dépôts Debian (2.1.x, syntaxe divergente sur certains opérateurs) ou une compilation opam de deux à trois heures sur ce SoC.

Raspberry Pi OS Lite 64 bits consomme environ 120 Mo au repos.

### 4.5 Refroidissement et alimentation

- Dissipateur obligatoire. Le BCM2837 throttle à 80 °C, et le boîtier fermé ne pardonne pas.
- Alimentation officielle 5,1 V / 2,5 A.
- Carte son alimentée par son propre bloc secteur plutôt que par le bus USB.
- Onduleur line-interactive piloté par NUT (§5.5).

### 4.6 Quand basculer vers un Pi 4

Trois seuils : xruns persistants après réglage des buffers, besoin d'un second flux, passage aux archives ou à une interface web. Tant que le projet reste sur un MP3 mono-flux avec programmation poussée en SSH, le 3B tient l'année.

---

## 5. Système

### 5.1 Installation

Raspberry Pi OS Lite 64 bits, SSH activé dès l'écriture de l'image.

```bash
apt update && apt full-upgrade
apt install --no-install-recommends \
  liquidsoap socat rsync curl ffmpeg cpufrequtils \
  systemd-timesyncd unattended-upgrades logrotate
```

Liquidsoap des dépôts est souvent en retard. Par ordre de préférence : le `.deb` arm64 des releases Savonet, sinon l'image Docker officielle (multi-arch), sinon opam. Vérifier :

```bash
liquidsoap --version      # viser 2.2.x minimum
```

En 2.1.x, la ligne `antenne.on_metadata(f)` du script devient `antenne = on_metadata(f, antenne)`.

### 5.2 Réglages firmware

`/boot/firmware/config.txt` :

```ini
dtparam=audio=off          # tue snd_bcm2835, libère l'index 0 pour la carte USB
dtoverlay=disable-wifi     # si Ethernet
dtoverlay=disable-bt
dtparam=watchdog=on        # watchdog matériel BCM
gpu_mem=16
```

`/boot/firmware/cmdline.txt`, en fin de ligne :

```
usbcore.autosuspend=-1
```

Sans ce paramètre, le noyau endort l'interface audio après quelques heures d'inactivité et le flux part en silence.

`/etc/modprobe.d/snd-usb.conf` :

```ini
options snd-usb-audio nrpacks=1
```

Correctif classique des xruns USB audio sur Pi.

Gouverneur CPU fixe, le scaling dynamique du BCM2837 suffit à provoquer des décrochages :

```bash
echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils
systemctl restart cpufrequtils
```

### 5.3 Watchdog matériel

Le seul mécanisme qui rattrape un gel noyau complet. Dans `/etc/systemd/system.conf` :

```ini
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
```

Le SoC redémarre seul si systemd cesse de le nourrir.

### 5.4 Survie de la carte SD

```bash
# journald en RAM
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n' \
  > /etc/systemd/journald.conf.d/volatile.conf

# swap désactivé
systemctl disable --now dphys-swapfile
```

`/etc/fstab`, sur la partition racine : `defaults,noatime,commit=120`.

Amélioration gratuite, à faire une fois : le Pi 3B sait booter sur USB, mais le fusible OTP doit être grillé avec `program_usb_boot_mode=1` dans `config.txt`, suivi d'un redémarrage puis du retrait de la ligne. Opération irréversible et sans effet de bord, qui ouvre ensuite la voie à un SSD USB et à l'oubli définitif de la corruption de carte SD.

### 5.5 Onduleur

```bash
apt install nut
```

`/etc/nut/ups.conf` :

```ini
[onduleur]
  driver = usbhid-ups
  port = auto
  desc = "Onduleur boîtier radio"
```

Puis `MODE=standalone` dans `/etc/nut/nut.conf`, et un `upsmon` qui déclenche l'arrêt propre à 20 % de batterie. Sans cela, une coupure longue finit par corrompre le système de fichiers.

### 5.6 Accès distant

Ouvrir un port SSH sur Internet pour une machine posée dans un local partagé est une mauvaise idée. Tailscale règle le problème sans redirection de port :

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --ssh --hostname=boitier-radio
```

Durcissement de sshd en complément :

```text
PasswordAuthentication no
PermitRootLogin no
AllowUsers radio
```

### 5.7 Utilisateur et arborescence

```bash
useradd -r -m -d /srv/radio -G audio -s /bin/bash radio
mkdir -p /srv/radio/{media/{musique,jingles,emissions,nuit,secours},playlists,queue}
mkdir -p /var/log/liquidsoap && chown radio:audio /var/log/liquidsoap
```

---

## 6. Carte son

La carte est déjà disponible. Points à valider avant tout le reste :

```bash
lsusb -v | grep -i audio          # class-compliant UAC1/UAC2 ?
arecord -l                        # apparaît-elle en capture ?
cat /proc/asound/cards            # nom exact, entre crochets
cat /proc/asound/card1/stream0    # fréquences natives supportées
arecord -D hw:CARD=XXX,DEV=0 -f cd -d 10 /tmp/test.wav
alsamixer -c 1                    # niveau d'entrée, désactiver un éventuel AGC
```

Deux précautions :

- Référencer la carte par son nom (`hw:CARD=XXX`) et jamais par son index (`hw:1,0`), l'index bouge au redémarrage.
- Relever la fréquence native. Le rééchantillonnage temps réel est le poste le plus coûteux sur ce processeur. Si la carte sort en 48 kHz, aligner tout le graphe dessus plutôt que de convertir.

---

## 7. Organisation des médias

```text
/srv/radio/
├── media/
│   ├── musique/        # rotation générale, un dossier = une playlist
│   ├── jingles/        # habillage, inséré par rotation
│   ├── emissions/      # émissions préenregistrées, lecture séquentielle
│   ├── nuit/           # programme 0h–6h
│   └── secours/boucle.mp3
├── playlists/          # fichiers .m3u pour un ordre exact
├── queue/              # dépôt d'un fichier = passage prioritaire
└── now.json            # titre en cours, pour le site
```

Deux façons de constituer une playlist. Le **dossier** suffit dans la grande majorité des cas, l'ordre étant géré par Liquidsoap. Le fichier **`.m3u`** dans `playlists/` sert quand l'ordre exact compte, pour une émission montée ou une soirée thématique.

Convention de nommage pour des métadonnées propres à l'antenne : `Artiste - Titre.mp3`, avec des tags ID3 corrects. Les tags priment sur le nom de fichier.

---

## 8. Le script Liquidsoap

`/srv/radio/radio.liq` — calibré pour le Pi 3B, à vérifier avec `liquidsoap --check`.

```liquidsoap
#!/usr/bin/env liquidsoap
# radio.liq — Liquidsoap 2.2+ — hôte Raspberry Pi 3B

# ---------- Réglages généraux ----------
settings.log.level        := 3
settings.log.file         := true
settings.log.file.path    := "/var/log/liquidsoap/radio.log"
settings.log.stdout       := true

# Socket de commande locale, pilotable en SSH via socat
settings.server.socket             := true
settings.server.socket.path        := "/run/liquidsoap/radio.sock"
settings.server.socket.permissions := 0o660

# Pi 3B : frame plus longue que la valeur par défaut, moins d'appels système
settings.frame.duration := 0.08

# Aligner sur la fréquence native de la carte son (cf. /proc/asound/cardN/stream0)
# pour supprimer tout rééchantillonnage temps réel.
settings.frame.audio.samplerate := 48000

base = "/srv/radio"

# ---------- Sources fichiers ----------
def dossier(~mode="randomize", name) =
  playlist(id=name, mode=mode, reload_mode="watch", "#{base}/media/#{name}")
end

# reload_mode="watch" : inotify recharge la playlist dès qu'un fichier arrive
# ou disparaît. Aucun redémarrage nécessaire après un rsync.

musique   = dossier("musique")
jingles   = dossier("jingles")
nuit      = dossier("nuit")
emissions = dossier(mode="normal", "emissions")

# Normalisation par les tags ReplayGain posés en amont (§9.1), nettement
# moins destructeur qu'un AGC temps réel et gratuit en CPU.
def rg(s) = amplify(1., override="replaygain_track_gain", s) end
musique = rg(musique)
nuit    = rg(nuit)

# ---------- Grille ----------
# 6 titres puis 1 jingle
journee = rotate(weights=[6, 1], [musique, jingles])

grille = switch(track_sensitive=true, [
  ({ 0h-6h },                  nuit),
  ({ 18h-20h and (2w or 4w) }, emissions),   # mardi et jeudi
  ({ true },                   journee)
])

# ---------- File d'attente prioritaire ----------
# echo "urgence.push /srv/radio/queue/flash.mp3" \
#   | socat - UNIX-CONNECT:/run/liquidsoap/radio.sock
urgence = request.queue(id="urgence")

programme = fallback(id="programme", track_sensitive=true, [urgence, grille])

# ---------- Entrée live ----------
live_in = input.alsa(id="live", device="hw:CARD=XXX,DEV=0", bufferize=true)

# buffer() isole l'horloge de la carte son de celle du reste du graphe.
# Marge large sur Pi 3B, où le bus USB est partagé avec l'Ethernet.
live = buffer(buffer=3., max=15., live_in)

# Le live ne prend l'antenne que s'il y a réellement du signal.
live = blank.strip(id="detection_silence",
                   threshold=-45., max_blank=20., min_noise=3., live)

# ---------- Antenne ----------
antenne = fallback(id="antenne", track_sensitive=false, [live, programme])
antenne = fallback(id="filet", track_sensitive=false,
                   [antenne, single("#{base}/media/secours/boucle.mp3")])
antenne = mksafe(antenne)

antenne = crossfade(duration=4., fade_in=1., fade_out=3., antenne)
antenne = limit(threshold=-1.5, antenne)

# ---------- Titre en cours pour le site ----------
# En 2.1.x : antenne = on_metadata(f, antenne)
antenne.on_metadata(fun (m) ->
  file.write(data=json.stringify({
    title  = m["title"],
    artist = m["artist"],
    at     = time()
  }), "#{base}/now.json"))

# ---------- Sortie Icecast ----------
# Un seul flux en V1. Pas d'archivage local, pas d'Opus en parallèle :
# écritures microSD et charge CPU réservées au Pi 4.
output.icecast(
  %mp3(bitrate=128, samplerate=48000, stereo=true),
  id="sortie_mp3",
  host     = environment.get("ICECAST_HOST"),
  port     = int_of_string(environment.get("ICECAST_PORT")),
  password = environment.get("ICECAST_PASSWORD"),
  mount    = environment.get("ICECAST_MOUNT"),
  name        = environment.get("RADIO_NAME"),
  description = environment.get("RADIO_DESC"),
  url         = environment.get("RADIO_URL"),
  genre    = "Varié",
  encoding = "UTF-8",
  fallible = false,
  on_error = fun (_) -> begin log("Icecast injoignable, nouvelle tentative") ; 5. end,
  antenne
)
```

Ce qui distingue un flux qui tient d'un flux qui tombe :

- `buffer()` sur l'entrée ALSA, contre la dérive d'horloge entre la carte son et le reste du graphe.
- `mksafe()` en bout de chaîne, qui garantit une source toujours disponible.
- `on_error` renvoyant un délai, ce qui déclenche la reconnexion sans faire tomber le processus.
- `fallible=false` sur la sortie, pour que Liquidsoap refuse de démarrer sans source valide plutôt que de streamer du vide.

---

## 9. Envoi des playlists en SSH

### 9.1 Script de déploiement (poste de travail)

`~/bin/radio-push` :

```bash
#!/usr/bin/env bash
set -euo pipefail

HOTE="radio@boitier-radio"
SRC="${HOME}/Radio/media/"
DST="/srv/radio/media/"

# 1. Tags de loudness EBU R128 calculés localement, zéro CPU sur le Pi
loudgain -a -k -s e $(find "$SRC" -type f \( -name '*.mp3' -o -name '*.flac' \))

# 2. Synchronisation bridée : le bus USB du Pi 3B est partagé avec l'Ethernet.
#    Sans --bwlimit, un gros transfert provoque des xruns à l'antenne.
nice -n 10 rsync -avh --partial --info=progress2 \
      --bwlimit=1500 \
      --delete \
      --exclude '.DS_Store' --exclude '._*' \
      -e 'ssh -o Compression=no' \
      "$SRC" "${HOTE}:${DST}"

# 3. Filet de sécurité si inotify a raté quelque chose
ssh "$HOTE" 'for p in musique jingles emissions nuit; do
  echo "$p.reload" | socat - UNIX-CONNECT:/run/liquidsoap/radio.sock
done'

echo "Déploiement terminé."
```

`--bwlimit=1500` plafonne à 1,5 Mo/s, assez pour pousser une playlist et assez bas pour ne pas perturber la capture. Surveiller le premier gros envoi :

```bash
watch -n1 'grep -c xrun /var/log/liquidsoap/radio.log'
```

Si le compteur bouge pendant le transfert, descendre à 800. `--delete` supprime sur le boîtier ce qui a disparu localement : tester d'abord avec `--dry-run`.

### 9.2 Pilotage à distance

```bash
S='socat - UNIX-CONNECT:/run/liquidsoap/radio.sock'

ssh radio@boitier-radio "echo 'help' | $S"                  # commandes disponibles
ssh radio@boitier-radio "echo 'sortie_mp3.metadata' | $S"   # titre en cours
ssh radio@boitier-radio "echo 'musique.skip' | $S"          # passer au suivant
ssh radio@boitier-radio "echo 'urgence.push /srv/radio/queue/flash.mp3' | $S"
ssh radio@boitier-radio 'journalctl -u radio -f'            # journal en direct
```

Un alias par commande dans `~/.ssh/config` et un `Makefile` local rendent l'ensemble utilisable au quotidien.

### 9.3 Versionner la grille

`radio.liq` dans un dépôt git, déployé par un second script :

```bash
scp radio.liq radio@boitier-radio:/srv/radio/radio.liq
ssh radio@boitier-radio \
  'liquidsoap --check /srv/radio/radio.liq && sudo systemctl restart radio'
```

Le `--check` avant redémarrage évite de couper l'antenne sur une faute de frappe.

---

## 10. Service systemd, secrets, surveillance

### 10.1 Secrets

`/etc/radio/radio.env`, en `chmod 600 root:root` :

```ini
ICECAST_HOST=radio.exemple.org
ICECAST_PORT=8000
ICECAST_MOUNT=/radio.mp3
ICECAST_PASSWORD=xxxxxxxx
RADIO_NAME=Ma radio
RADIO_DESC=Description courte
RADIO_URL=https://radio.exemple.org
```

Le mot de passe source ne doit jamais apparaître dans `radio.liq`, qui a vocation à finir dans un dépôt git.

### 10.2 Unité

`/etc/systemd/system/radio.service` :

```ini
[Unit]
Description=Liquidsoap - flux radio
After=network-online.target sound.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=radio
Group=audio
EnvironmentFile=/etc/radio/radio.env
ExecStartPre=/usr/bin/liquidsoap --check /srv/radio/radio.liq
ExecStart=/usr/bin/liquidsoap /srv/radio/radio.liq
Restart=always
RestartSec=5
RuntimeDirectory=liquidsoap
RuntimeDirectoryMode=0770
Nice=-5
LimitRTPRIO=95
LimitMEMLOCK=infinity
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/radio /var/log/liquidsoap

[Install]
WantedBy=multi-user.target
```

`ExecStartPre` avec `--check` évite qu'une erreur de syntaxe pousse systemd dans une boucle de redémarrage.

### 10.3 Healthcheck actif

`Restart=always` ne couvre pas le cas le plus fréquent : le processus est vivant, mais le flux ne sort plus. Un contrôle indépendant s'impose.

`/usr/local/bin/radio-healthcheck` :

```bash
#!/usr/bin/env bash
set -euo pipefail
source /etc/radio/radio.env

# Alimentation : 0x0 attendu, bit 16 = sous-tension déjà survenue
THROTTLED=$(vcgencmd get_throttled)
[ "$THROTTLED" = "throttled=0x0" ] || logger -t radio-healthcheck "$THROTTLED"

# Ne rien faire si c'est Internet qui est coupé, Liquidsoap se reconnectera seul
ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 || exit 0

if ! curl -fsS --max-time 10 \
     "http://${ICECAST_HOST}:${ICECAST_PORT}/status-json.xsl" \
     | grep -q -- "${ICECAST_MOUNT}"; then
  logger -t radio-healthcheck "Point de montage absent, redémarrage du service"
  systemctl restart radio.service
fi
```

Timer toutes les deux minutes :

```ini
# /etc/systemd/system/radio-healthcheck.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
```

### 10.4 Alerte externe

Un ping vers healthchecks.io depuis le healthcheck, ou une instance Uptime Kuma sur le VPS, envoie un mail quand le flux disparaît. Sans alerte, une panne de nuit dure jusqu'au matin.

---

## 11. Serveur Icecast

### 11.1 Dimensionnement

Bande passante = bitrate × auditeurs simultanés.

| Auditeurs simultanés | Débit sortant | Volume mensuel 24/7 |
|---:|---:|---:|
| 10 | 1,3 Mb/s | ~415 Go |
| 50 | 6,4 Mb/s | ~2,1 To |
| 200 | 25,6 Mb/s | ~8,3 To |

Un VPS à 4–6 €/mois avec trafic généreux tient plusieurs centaines d'auditeurs. Le CPU n'est pas le facteur limitant, Icecast se contente de recopier des octets.

### 11.2 Configuration essentielle

```xml
<limits>
  <clients>500</clients>
  <sources>4</sources>
  <burst-size>65536</burst-size>
</limits>

<mount type="normal">
  <mount-name>/radio.mp3</mount-name>
  <fallback-mount>/secours.mp3</fallback-mount>
  <fallback-override>1</fallback-override>
  <fallback-when-full>1</fallback-when-full>
  <charset>UTF-8</charset>
</mount>
```

`fallback-override` est le point clé. Quand le boîtier disparaît, les auditeurs glissent automatiquement sur `/secours.mp3` **sans perdre leur connexion**, et reviennent sur le direct dès son retour. Condition impérative : codec, débit et fréquence d'échantillonnage strictement identiques entre les deux flux. Si le Pi émet en 48 kHz, le secours doit l'être aussi.

`/secours.mp3` est alimenté par une petite instance Liquidsoap sur le VPS :

```liquidsoap
settings.frame.audio.samplerate := 48000
secours = mksafe(playlist(mode="randomize", reload_mode="watch", "/srv/secours"))
output.icecast(%mp3(bitrate=128, samplerate=48000, stereo=true),
  host="localhost", port=8000, password=..., mount="/secours.mp3", secours)
```

### 11.3 HTTPS

Un site en HTTPS ne peut pas lire un flux en HTTP, le navigateur bloque le contenu mixte. Reverse proxy Caddy, certificat automatique :

```caddy
radio.exemple.org {
    reverse_proxy 127.0.0.1:8000
    header Access-Control-Allow-Origin *
}
```

Le flux public devient `https://radio.exemple.org/radio.mp3`, utilisable dans un `<audio>` sur n'importe quel site.

---

## 12. Le secours, à deux niveaux

| Panne | Ce qui prend le relais | Délai | Auditeur déconnecté ? |
|---|---|---|---|
| Silence au studio | `blank.strip` bascule sur la grille auto | 20 s | non |
| Grille vide ou média manquant | `single(boucle.mp3)` puis `mksafe` | immédiat | non |
| Liquidsoap plante | systemd redémarre le service | 5–15 s | non (fallback serveur) |
| Gel noyau | watchdog matériel BCM | ~15 s puis reboot | non (fallback serveur) |
| Boîtier ou lien Internet coupé | `fallback-mount` côté Icecast | immédiat | non |
| Coupure secteur | onduleur, arrêt propre, redémarrage automatique | durée de la coupure | non (fallback serveur) |

C'est la combinaison locale et distante qui distingue une bidouille d'une installation exploitable.

---

## 13. Budget

| Poste | Coût |
|---|---:|
| Raspberry Pi 3B | 0 € (disponible) |
| microSD dédiée 32 Go | 8 € |
| Dissipateur | 3 € |
| Alimentation officielle 2,5 A | 0–10 € |
| Carte son USB | 0 € (disponible) |
| Onduleur | 0 € (disponible) |
| Câblage, adaptateurs | 10 € |
| **Total matériel** | **20–30 €** |
| VPS Icecast | 4–6 €/mois |
| Nom de domaine | ~10 €/an |

Le poste récurrent est le serveur, pas le boîtier.

---

## 14. Feuille de route et critères de recette

### V1 — Flux stable
Carte son reconnue, Liquidsoap encode, Icecast reçoit.
**Recette** : 24 h de diffusion continue, aucun xrun dans les logs, `vcgencmd get_throttled` à `0x0`.

### V2 — Autonomie
systemd, healthcheck, watchdog, secours local et serveur, onduleur sous NUT.
**Recette** : débrancher le secteur, débrancher le réseau, tuer le processus. Le flux revient seul dans chaque cas, sans déconnecter les auditeurs.

### V3 — Automate complet
Grille horaire, rotation, jingles, file prioritaire, ReplayGain.
**Recette** : une semaine sans intervention humaine, avec la bonne émission au bon créneau.

### V4 — Exploitation
Workflow `radio-push` documenté, `now.json` branché au site, alerte externe.
**Recette** : quelqu'un d'autre publie une playlist en suivant la doc, sans aide.

### V5 — Migration matérielle
Boot USB, SSD, archivage horaire, second flux Opus. Conditionne le passage à un Pi 4 (§17).

---

## 15. Mise en route

```bash
# 1. Système
apt install --no-install-recommends liquidsoap socat rsync curl ffmpeg cpufrequtils
useradd -r -m -d /srv/radio -G audio radio

# 2. Firmware (§5.2), puis redémarrage

# 3. Audio
arecord -l && cat /proc/asound/cards
cat /proc/asound/card1/stream0
arecord -D hw:CARD=XXX,DEV=0 -f cd -d 10 /tmp/test.wav && aplay /tmp/test.wav

# 4. Script et secrets
install -m 640 -o radio -g audio radio.liq /srv/radio/radio.liq
install -m 600 radio.env /etc/radio/radio.env
liquidsoap --check /srv/radio/radio.liq

# 5. Test manuel avant de créer le service
sudo -u radio env $(cat /etc/radio/radio.env | xargs) liquidsoap /srv/radio/radio.liq

# 6. Service
systemctl enable --now radio.service radio-healthcheck.timer
journalctl -u radio -f
```

---

## 16. Dépannage

| Symptôme | Cause probable | Vérification |
|---|---|---|
| `unknown PCM hw:...` | index de carte changé | `cat /proc/asound/cards`, passer en `hw:CARD=` |
| Craquements pendant un `rsync` | bus USB saturé | baisser `--bwlimit` à 800 |
| Craquements réguliers hors transfert | xruns ALSA | monter `settings.frame.duration` à 0.12, vérifier `nrpacks=1` |
| Silence au bout de quelques heures | carte son mise en veille | `usbcore.autosuspend=-1` présent ? |
| Dérive puis coupure après plusieurs heures | horloges désynchronisées | présence de `buffer()` sur l'entrée live |
| Redémarrages inexpliqués | sous-tension | `vcgencmd get_throttled`, alim et carte son bus-powered |
| Ralentissements progressifs | throttling thermique | `vcgencmd measure_temp`, dissipateur |
| CPU anormalement haut | rééchantillonnage | aligner `settings.frame.audio.samplerate` sur la carte |
| Volume irrégulier entre titres | ReplayGain absent | relancer `loudgain` puis `radio-push` |
| Player muet sur le site | contenu mixte HTTP/HTTPS | passer par le reverse proxy TLS |
| Service en boucle de redémarrage | erreur de syntaxe | `liquidsoap --check`, `journalctl -u radio -n 50` |

---

## 17. Évolutions matérielles

### Pi 4 (4 Go)

Bus USB séparé de l'Ethernet, ce qui supprime la contrainte de `--bwlimit`. Permet l'archivage horaire, un second flux Opus, et un Icecast local en secours de dernier recours. Boot NVMe possible sur Pi 5.

La console multi-personnes n'est pas sur le boîtier : c'est Radiotomate sur la VM Labomedia (prod BUTTON). Le Pi, s'il sert encore, n'est qu'un encodeur live vers le harbor `:6800`.

---

## 18. Prochaine action

Écrire une microSD Raspberry Pi OS Lite 64 bits, puis valider la carte son :

```bash
arecord -l
cat /proc/asound/card1/stream0
arecord -D hw:CARD=XXX,DEV=0 -f cd -d 10 /tmp/test.wav
```

Le résultat de ces trois commandes détermine le reste : périphérique ALSA à écrire dans `radio.liq`, fréquence d'échantillonnage à aligner dans tout le graphe, et confirmation que le 3B tient la capture sans xrun.
