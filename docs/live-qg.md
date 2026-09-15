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

## Recette

1. Auto-DJ en cours sur le player public.
2. BUTT / Liquidsoap connecte → crossfade vers le live (5–30 s).
3. Stop encodeur → retour auto-DJ sur un **nouveau** titre (pas reprise au milieu).
4. Si ça coupe : logs `playout.log`, augmenter les buffers, vérifier le rôle `stream`.

Test local : `ops/local/compose.yml` expose 6800 factice seulement si le playout tourne. Sur Mac, valider d’abord Icecast Labomedia / VM Linux.
