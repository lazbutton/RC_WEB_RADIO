#!/usr/bin/env python3
"""Now Playing sidecar. Relais Radiotomate metadata_log → GET /now.json (CORS)."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("RT_NOWPLAYING_PORT", "6820"))
SECRET = os.environ.get("RT_NOWPLAYING_SECRET", "ntr-dev-secret")
STATE = Path(os.environ.get("RT_NOWPLAYING_FILE", str(Path(__file__).resolve().parent / "now.json")))
LOCK = threading.Lock()

MOCK = {
    "artist": "New Trad Radio",
    "title": "En attente de métadonnées",
    "album": "",
    "source": "autodj",
    "on_air": datetime.now(timezone.utc).isoformat(),
    "SOURCE_NAME": "new-trad-radio",
}


def unpack_playlist_as_artist(data: dict) -> dict:
    """Radiotomate / ancien streamer : artist = nom de cart, title = « Artiste - Titre »."""
    out = dict(data)
    title = str(out.get("title") or "").strip()
    artist = str(out.get("artist") or "").strip()
    album = str(out.get("album") or "").strip()
    if " - " not in title:
        return out
    left, right = (part.strip() for part in title.split(" - ", 1))
    if not left or not right:
        return out
    if not artist or (album and artist == album):
        if album or artist:
            out["album"] = album or artist
        out["artist"] = left
        out["title"] = right
    return out


def load_state() -> dict:
    if STATE.exists():
        try:
            return unpack_playlist_as_artist(json.loads(STATE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return dict(MOCK)


def save_state(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/now.json", "/now", "/"):
            with LOCK:
                body = json.dumps(load_state(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/hook":
            self.send_error(404)
            return
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {SECRET}"
        if SECRET and auth != expected:
            self.send_error(401)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        data: dict
        if "json" in ctype:
            data = json.loads(raw.decode("utf-8") or "{}")
        else:
            from urllib.parse import parse_qs

            parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            data = {k: v[-1] if v else "" for k, v in parsed.items()}
        data["received_at"] = datetime.now(timezone.utc).isoformat()
        data = unpack_playlist_as_artist(data)
        with LOCK:
            save_state(data)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[nowplaying] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    if not STATE.exists():
        save_state(dict(MOCK))
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Now Playing on http://127.0.0.1:{PORT}/now.json")
    httpd.serve_forever()
