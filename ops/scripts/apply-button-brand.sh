#!/usr/bin/env bash
# Pose INTERFACE_NAME en base (install amont non forkée).
set -euo pipefail
DB="${1:?usage: apply-button-brand.sh /path/to/radiotomate.db}"
sqlite3 "$DB" "INSERT INTO settings(key,value) VALUES('INTERFACE_NAME','BUTTON') ON CONFLICT(key) DO UPDATE SET value=excluded.value;"
sqlite3 "$DB" "SELECT key,value FROM settings WHERE key='INTERFACE_NAME';"
