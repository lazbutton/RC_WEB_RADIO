# Live QG — recette port 6800

Radiotomate écoute un flux **comme Icecast** : `hôte:6800` mount `stream` (YAML `input_name`). Priorité max : dès qu’un encodeur autorisé connecte, l’auto-DJ / les carts cèdent.

## Compte

Utilisateur avec le rôle **`stream`**. Identifiants = ceux de l’interface (pas le mot de passe source Icecast Labomedia).

Le scheduler appelle `POST /can_stream` avec user/password avant d’accepter le harbor.

## Réseau

En prod, Caddy reverse-proxy TCP/HTTPS vers `:6800`. L’encodeur vise le hostname public, **pas** `localhost` du Mac.

Buffer YAML (retard d’antenne) :

```yaml
playout_process_config:
  input_name: stream
  input_min_buffer: 5.
  input_max_buffer: 30.
```

Ligne instable → « bégaiement » vers l’auto-DJ. Monter `input_min_buffer` progressivement. Démarrer **5–10 s avant** l’heure d’antenne.

## BUTT (laptop QG)

- Serveur : `radio.example.labomedia.org` (hôte Caddy)
- Port : **443** si TLS proxy, ou **6800** en direct
- Type : Icecast
- Mount : `/stream`
- User / password : compte `qg-st-aignan`
- Format : MP3 CBR 128 ou 192, stéréo, **48 kHz** si la iD14 est en 48 kHz

## Liquidsoap (Pi 3B encodeur)

L’ancien graphe playlist Pi (`docs/archive/pi-boitier/radio.liq`) sortait en `output.dummy` : ce n’est **pas** la recette QG. Pour le live festival, une sortie Icecast **vers le harbor Radiotomate** :

```liquidsoap
output.icecast(
  %mp3(bitrate=128, samplerate=48000),
  host="RADIOMATE_HOST",
  port=6800,
  user="qg-st-aignan",
  password="...",
  mount="/stream",
  input.alsa(device="plughw:CARD=iD14,DEV=0")
)
```

Prérequis Pi : alim 5,1 V saine (`vcgencmd get_throttled` → `0x0`), iD14 sur **son** secteur, dissipateur. Sans ça : xruns et drop live.

## Voiceover micro Hub (ducking) — ce qui existe dans le `.liq`

Distinct du harbor : le micro du Hub ne *prend* pas l’antenne, il **parle par-dessus** le bed (auto-DJ / carts / jingles), jamais par-dessus un direct harbor.

- Entrée : PCM brut poussé en TCP sur `:6802` (`input.ffmpeg` en écoute, `voiceover_pcm`), mixé au bed (`add`), pas de fichier.
- Ducking : `POST /voiceover {"on": true, "fade": 1.2}` sur l’API playout (`:6833`, token) → le bed descend à **0,18** (≈ −15 dB) en `fade` s ; `{"on": false}` remonte. `GET /voiceover` renvoie `on`, `gain`, `fade`, `remaining`.
- Garde-fous côté Liquidsoap : refus (409) si la source est `stream` (direct), et **retour automatique** du niveau quand il reste ≤ 15 s au titre (`VOICEOVER_RESTORE_S`) pour ne pas ducker l’enchaînement.
- Le Hub y accède via nowplaying (`/probe/voiceover`, `/probe/voiceover/pcm` dans `button/hub/nginx.conf`) ; le playout n’est jamais exposé.

Recette : titre en cours → `on` → niveau bed −15 dB en ~1 s, micro audible → `off` → retour ; lancer un direct harbor pendant `on` → le bed ne ducke plus (la priorité live gagne).

## État réel (sept. 2026)

- Playout : **Liquidsoap 2.4.5** sur Nasgul. Le commentaire « transitions cassées LS 2.3 (#4179) » dans le `.liq` date de 2.3 : `crossfade_3s` ne fait **aucun** fondu (il ne fait que noter la source à l’antenne). À re-tester sur banc avant d’activer un vrai `cross` ; les jingles coupent net (« ducking jingles » = chantier E).
- Harbor : recette BUTT **pas encore faite** contre Nasgul (`192.168.1.100:6800` ou Tailscale). Le retour de direct vide `jingles` + `autodj` et laisse la file `carts`.
- Transitions : le bug amont [savonet/liquidsoap#4179](https://github.com/savonet/liquidsoap/issues/4179) (transitions de `fallback` inopérantes depuis 2.3) est **toujours ouvert** ; ne pas remettre `fade.in`/`fade.out` dans `crossfade_3s` sans banc. Banc sans toucher la prod : `docker run --rm -v <liq>:/t.liq -e RTCONFIG=… ghcr.io/lazbutton/button-playout:main liquidsoap /t.liq` sur Nasgul avec deux WAV et un harbor sur un autre port.
- Ducking jingles : même dépendance au banc. Piste : `add([amplify({duck}, autodj), jingles])` à la place du `fallback` interne, avec `current_source_id` posé par `jingles.on_track` — à écouter avant de coder, car ça change le sens de « jingle à l’antenne » pour le conducteur.

## EF-01 — créneau live sans encodeur : le filet est l’horloge

Décision : **le filet d’un créneau `live` est la grille elle-même** (daypart → horloge → cart de repli), qui couvre 24/24. Rien n’est poussé « par accident » : à H, si le harbor n’est pas là, l’horloge continue, jamais de `blank()` ni de file vide. Ce qui manquait, c’est de le *déclarer* et de le *voir*.

- Créneaux live = `Emission` hebdo (jour, début, fin), édités dans la console (Semaine).
- `alerts.py` : après `live_grace_seconds` (90 s par défaut) sans source harbor pendant un créneau actif, incident **`live_absent`** « EF-01 QG St Aignan 20:00-22:00 : encodeur absent, filet horloge à l’antenne (carts) » → log, webhook, tâche Vikunja ; **rétabli** dès que l’encodeur connecte ou que le créneau finit. `playout_down` prime (pas de double alerte).
- Recette : créer une émission hebdo sur le créneau courant, attendre 2 min sans BUTT → tâche Vikunja ; lancer BUTT → tâche close.

## Recette BUTT contre Nasgul (à faire)

1. Compte Radiotomate avec rôle `stream` (`radiotomate users add qg --can stream`, dans `button-radiotomate-interface-1`).
2. BUTT : serveur `192.168.1.100` (ou l’IP Tailscale de Nasgul), port **6800**, type Icecast, mount `/stream`, user/pass du compte, MP3 128–192 k, 48 kHz.
3. Vérifier `GET :6822/live` → `source: stream` ; le player `now.json` doit afficher l’émission de la session.
4. Stop BUTT → retour sur un **nouveau** titre (`autodj`/`jingles` vidés, `carts` gardée) ; noter le temps de bascule et tout « bégaiement » (monter `input_min_buffer` si besoin).
5. Rejouer avec l’alerte EF-01 armée (créneau hebdo courant) pour valider l’aller-retour incident.

## Recette

1. Auto-DJ en cours sur le player public.
2. BUTT / Liquidsoap connecte → crossfade vers le live (5–30 s).
3. Stop encodeur → retour auto-DJ sur un **nouveau** titre (pas reprise au milieu).
4. Si ça coupe : logs `playout.log`, augmenter les buffers, vérifier le rôle `stream`.

Test local : `ops/local/compose.yml` expose 6800 factice seulement si le playout tourne. Sur Mac, valider d’abord Icecast Labomedia / VM Linux.
