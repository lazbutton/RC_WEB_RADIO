#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-${DATA_ROOT:-./radiotomate_data}/MusicDropbox}"
mkdir -p "$ROOT"/{ntf1,ntf2,ntf3,rotation,habits}
printf "Dropbox NTR : %s\n" "$ROOT"
find "$ROOT" -type d | sort
