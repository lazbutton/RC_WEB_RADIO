#!/usr/bin/env liquidsoap
# radio.liq — Liquidsoap 2.3 — hôte Raspberry Pi 3B (Audient iD14)
# Sortie Icecast commentée : graphe local seulement (output.dummy).

# ---------- Réglages généraux ----------
settings.log.level        := 3
settings.log.file         := true
settings.log.file.path    := "/var/log/liquidsoap/radio.log"
settings.log.stdout       := true

settings.server.socket             := true
settings.server.socket.path        := "/run/liquidsoap/radio.sock"
settings.server.socket.permissions := 0o660

settings.frame.duration := 0.08

# iD14 natif : 44100, 48000, 88200, 96000 — pas de rééchantillonnage
settings.frame.audio.samplerate := 48000
# 12 canaux en capture USB ; le graphe reste stéréo via plughw
settings.frame.audio.channels := 2

base = "/srv/radio"

def dossier(~mode="randomize", name) =
  playlist(id=name, mode=mode, reload_mode="watch", "#{base}/media/#{name}")
end

musique   = dossier("musique")
jingles   = dossier("jingles")
nuit      = dossier("nuit")
emissions = dossier(mode="normal", "emissions")

def rg(s) = amplify(1., override="replaygain_track_gain", s) end
musique = rg(musique)
nuit    = rg(nuit)

# 6 titres puis 1 jingle (un fichier placeholder dans jingles/ au premier boot)
journee = rotate(weights=[6, 1], [musique, jingles])

grille = switch(track_sensitive=true, [
  ({ 0h-6h },                  nuit),
  ({ 18h-20h and (2w or 4w) }, emissions),
  ({ true },                   journee)
])

urgence = request.queue(id="urgence")
programme = fallback(id="programme", track_sensitive=true, [urgence, grille])

# plughw : iD14 = 12 canaux matériel → 2 canaux Liquidsoap
live_in = input.alsa(id="live", device="plughw:CARD=iD14,DEV=0")
live = buffer(buffer=3., max=15., live_in)
live = blank.strip(id="detection_silence",
                   threshold=-45., max_blank=20., min_noise=3., live)

antenne = fallback(id="antenne", track_sensitive=false, [live, programme])
antenne = fallback(id="filet", track_sensitive=false,
                   [antenne, single("#{base}/media/secours/boucle.mp3")])
antenne = mksafe(antenne)

antenne = crossfade(duration=4., fade_in=1., fade_out=3., antenne)
# crossfade redevient fallible : mksafe avant limit / dummy
antenne = mksafe(antenne)
antenne = limit(threshold=-1.5, antenne)

antenne.on_metadata(fun (m) ->
  file.write(data=json.stringify({
    title  = m["title"],
    artist = m["artist"],
    at     = time()
  }), "#{base}/now.json"))

# Icecast V1 — décommenter quand /etc/radio/radio.env est réel
# output.icecast(
#   %mp3(bitrate=128, samplerate=48000, stereo=true),
#   id="sortie_mp3",
#   host     = environment.get("ICECAST_HOST"),
#   port     = int_of_string(environment.get("ICECAST_PORT")),
#   password = environment.get("ICECAST_PASSWORD"),
#   mount    = environment.get("ICECAST_MOUNT"),
#   name        = environment.get("RADIO_NAME"),
#   description = environment.get("RADIO_DESC"),
#   url         = environment.get("RADIO_URL"),
#   genre    = "Varié",
#   encoding = "UTF-8",
#   fallible = false,
#   on_error = fun (_) -> begin log("Icecast injoignable, nouvelle tentative") ; 5. end,
#   antenne
# )

output.dummy(id="horloge", fallible=false, antenne)
