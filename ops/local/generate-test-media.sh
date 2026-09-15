#!/usr/bin/env bash
# Médias de test pour valider dropbox / jingles / auto-DJ (tags ID3 via ffmpeg).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DROP="${1:-$ROOT/ops/local/data/MusicDropbox}"
"$ROOT/ops/scripts/setup-dropbox.sh" "$DROP"

make_mp3() {
  local dest="$1" artist="$2" title="$3" seconds="$4" freq="$5"
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i "sine=frequency=${freq}:sample_rate=48000:duration=${seconds}" \
    -c:a libmp3lame -b:a 128k \
    -metadata artist="$artist" -metadata title="$title" \
    "$dest"
}

make_mp3 "$DROP/habits/NTR - Jingle test.mp3" "NTR" "Jingle test" 4 880
make_mp3 "$DROP/rotation/Test - Souffle.mp3" "Test" "Souffle" 12 440
make_mp3 "$DROP/rotation/Test - Contretemps.mp3" "Test" "Contretemps" 12 330
make_mp3 "$DROP/ntf1/Archive NTF1 - Plateau.mp3" "Archive NTF1" "Plateau" 8 220
make_mp3 "$DROP/ntf2/Archive NTF2 - Live.mp3" "Archive NTF2" "Live" 8 260
make_mp3 "$DROP/ntf3/Archive NTF3 - Interview.mp3" "Archive NTF3" "Interview" 8 294

echo "Fichiers de test :"
find "$DROP" -name '*.mp3' | sort
echo
echo "Sur une install Radiotomate Linux :"
echo "  rsync -a \"$DROP/\" \"\$DATA_ROOT/MusicDropbox/\""
echo "  → drop2beets importe rotation/ntf* ; habits/ va dans un cart jingles (UI)."
echo "Icecast local : http://127.0.0.1:18000/  source password ntrhackme"
echo "Now Playing : http://127.0.0.1:6820/now.json"
