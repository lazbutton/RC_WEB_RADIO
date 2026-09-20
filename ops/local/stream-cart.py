#!/usr/bin/env python3
"""Boucle le cart #2 vers Icecast local, avec artiste/titre ID3 (pas le nom de playlist)."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "radiotomate" / "radio_data"
DB = DATA / "radiotomate.db"
ICE = "icecast://source:buttonhackme@127.0.0.1:18000/button.mp3"
HOOK = "http://127.0.0.1:6820/hook"
CART_ID = 2
LEADING_INDEX = re.compile(r"^\d+\s+")


def tracks() -> list[tuple[str, str, float]]:
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT path, title, duration FROM sounds WHERE cart_id = ? AND active = 1 ORDER BY rank",
        (CART_ID,),
    ).fetchall()
    conn.close()
    return [(str(DATA / rel), title, float(dur or 0)) for rel, title, dur in rows]


def split_title(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if " - " not in text:
        return "", text
    artist, title = text.split(" - ", 1)
    return artist.strip(), title.strip()


def probe(path: str) -> tuple[str, str, float]:
    try:
        raw = subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            timeout=8,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return "", "", 0.0
    try:
        fmt = json.loads(raw).get("format") or {}
    except json.JSONDecodeError:
        return "", "", 0.0
    tags = fmt.get("tags") or {}
    lower = {str(k).lower(): str(v).strip() for k, v in tags.items()}
    artist = lower.get("artist") or ""
    title = LEADING_INDEX.sub("", lower.get("title") or "")
    if artist and title.lower().startswith(artist.lower() + " - "):
        title = title[len(artist) + 3 :].strip()
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return artist, title, duration


def now_playing(path: str, db_title: str, db_duration: float) -> tuple[str, str, float]:
    artist, title, duration = probe(path)
    if not (artist and title):
        fallback_artist, fallback_title = split_title(db_title)
        artist = artist or fallback_artist
        title = title or fallback_title or db_title
    return artist, title, duration or db_duration


def hook(artist: str, title: str, playlist: str, duration: float) -> None:
    body = json.dumps(
        {
            "artist": artist,
            "title": title,
            "album": playlist,
            "duration": round(duration, 3),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source": "cart",
            "SOURCE_NAME": "button",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        HOOK,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer button-dev-secret",
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.URLError as exc:
        print(f"[stream] nowplaying: {exc}", flush=True)


def playlist_name() -> str:
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT title FROM carts WHERE id = ?", (CART_ID,)).fetchone()
    conn.close()
    return row[0] if row else ""


def play(path: str, db_title: str, db_duration: float, playlist: str) -> None:
    artist, title, duration = now_playing(path, db_title, db_duration)
    print(f"[stream] → {artist} — {title} ({int(duration)}s)", flush=True)
    hook(artist, title, playlist, duration)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-i",
            path,
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-content_type",
            "audio/mpeg",
            "-ice_name",
            "BUTTON",
            "-ice_description",
            f"{artist} - {title}"[:80],
            "-f",
            "mp3",
            ICE,
        ],
        check=False,
    )


def main() -> None:
    items = tracks()
    if not items:
        raise SystemExit(f"Aucun son dans le cart #{CART_ID}")
    playlist = playlist_name()
    print(f"[stream] {len(items)} titres en boucle vers {ICE}", flush=True)
    while True:
        for path, db_title, db_duration in tracks():
            if not Path(path).exists():
                print(f"[stream] manquant: {path}", flush=True)
                continue
            play(path, db_title, db_duration, playlist)
            time.sleep(0.4)


if __name__ == "__main__":
    main()
