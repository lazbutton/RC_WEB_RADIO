#!/usr/bin/env python3
"""Now Playing sidecar. Relais Radiotomate metadata_log → GET /now.json (CORS)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PORT = int(os.environ.get("RT_NOWPLAYING_PORT", "6820"))
SECRET = os.environ.get("RT_NOWPLAYING_SECRET", "button-dev-secret")
STATE = Path(os.environ.get("RT_NOWPLAYING_FILE", str(Path(__file__).resolve().parent / "now.json")))
PLAYOUT_URL = os.environ.get("RT_PLAYOUT_URL", "http://radiotomate-playout:6833").rstrip("/")
PLAYOUT_PCM = os.environ.get("RT_PLAYOUT_PCM", "radiotomate-playout:6802")
PLAYOUT_YAML = Path(os.environ.get("RT_PLAYOUT_YAML", "/config/radiotomate.yaml"))
MEDIA_CANDIDATES = (
    os.environ.get("BUTTON_MEDIA_ROOT", "").strip(),
    "/media",
    "/mnt/data/media/button",
    "/mnt/data/media/button",
)
LOCK = threading.Lock()
NEXT_KEYS = ("next_autodj", "next_jingle", "next_cart")
CLOCK_KEYS = ("remaining", "elapsed", "duration")
WAITING_TITLE = "En attente de métadonnées"
WS_GUID = "258EAFA5-E914-47DA-8CCB-90CA6C34424D"

MOCK = {
    "artist": "BUTTON",
    "title": WAITING_TITLE,
    "album": "",
    "source": "autodj",
    "on_air": datetime.now(timezone.utc).isoformat(),
    "SOURCE_NAME": "new-trad-radio",
    "previous": None,
    "next_autodj": None,
    "next_jingle": None,
    "next_cart": None,
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


def _as_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0:
        return None
    return number


def _as_int(value: object, default: int = -1) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize_cue(value: object) -> dict | None:
    parsed = value
    if value is None or value == "" or value == {}:
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    rid = _as_int(parsed.get("rid", -1))
    title = str(parsed.get("title") or "").strip()
    artist = str(parsed.get("artist") or "").strip()
    album = str(parsed.get("album") or "").strip()
    if rid < 0 and not title and not artist:
        return None
    cue: dict = {"title": title, "artist": artist, "rid": rid}
    if album:
        cue["album"] = album
    uri = str(parsed.get("initial_uri") or "").strip()
    if uri:
        cue["initial_uri"] = uri
    duration = _as_float(parsed.get("duration"))
    if duration:
        cue["duration"] = duration
    return cue


def _has_track(data: dict) -> bool:
    return bool(str(data.get("title") or "").strip() or str(data.get("artist") or "").strip())


def is_sidecar_merge(data: dict) -> bool:
    has_next = any(key in data for key in NEXT_KEYS)
    has_clock = any(key in data for key in CLOCK_KEYS)
    return (has_next or has_clock) and not _has_track(data)


def _identity(data: dict) -> tuple:
    sound_id = str(data.get("radiotomate_sound_id") or "").strip()
    if sound_id:
        return ("id", sound_id)
    return (
        "ta",
        str(data.get("title") or "").strip(),
        str(data.get("artist") or "").strip(),
    )


def _track_duration(data: dict) -> float | None:
    duration = _as_float(data.get("duration"))
    if duration:
        return duration
    elapsed = _as_float(data.get("elapsed")) or 0.0
    remaining = _as_float(data.get("remaining")) or 0.0
    artist = str(data.get("artist") or "").strip().lower()
    source = str(data.get("source") or "").strip().lower()
    if "jingle" in artist or "jingle" in source:
        return elapsed if elapsed > 0 else None
    total = elapsed + remaining
    return total if total > 0 else None


def _stash_previous(current: dict) -> dict | None:
    title = str(current.get("title") or "").strip()
    artist = str(current.get("artist") or "").strip()
    if not title and not artist:
        return None
    if title == WAITING_TITLE:
        return None
    previous = {
        "title": title,
        "artist": artist,
        "album": str(current.get("album") or "").strip(),
        "source": str(current.get("source") or "").strip(),
    }
    sound_id = str(current.get("radiotomate_sound_id") or "").strip()
    if sound_id:
        previous["radiotomate_sound_id"] = sound_id
    duration = _track_duration(current)
    if duration:
        previous["duration"] = round(duration, 3)
    return previous


def _merge_clock(out: dict, incoming: dict) -> None:
    for key in CLOCK_KEYS:
        if key not in incoming:
            continue
        try:
            value = float(incoming[key])
        except (TypeError, ValueError):
            continue
        out[key] = value
    duration = _as_float(out.get("duration"))
    elapsed = _as_float(out.get("elapsed")) or 0.0
    if duration is not None and duration + 0.5 < elapsed:
        out.pop("duration", None)


def apply_hook(current: dict, incoming: dict) -> dict:
    payload = dict(incoming)
    payload["received_at"] = datetime.now(timezone.utc).isoformat()
    if is_sidecar_merge(payload):
        out = dict(current)
        for key in NEXT_KEYS:
            if key in payload:
                out[key] = normalize_cue(payload[key])
        _merge_clock(out, payload)
        out["received_at"] = payload["received_at"]
        return out

    payload = unpack_playlist_as_artist(payload)
    if _identity(current) != _identity(payload):
        previous = _stash_previous(current)
        if previous is not None:
            payload["previous"] = previous
        elif "previous" not in payload:
            payload["previous"] = current.get("previous")
    elif "previous" not in payload:
        payload["previous"] = current.get("previous")

    for key in NEXT_KEYS:
        if key in payload:
            payload[key] = normalize_cue(payload[key])
        else:
            payload[key] = current.get(key)
    _merge_clock(payload, current)
    _merge_clock(payload, incoming)
    return payload


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


def playout_token() -> str:
    env = os.environ.get("RT_PLAYOUT_TOKEN", "").strip()
    if env:
        return env
    if not PLAYOUT_YAML.exists():
        return ""
    in_block = False
    for raw in PLAYOUT_YAML.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        stripped = line.strip()
        if stripped.startswith("playout_process_config"):
            in_block = True
            continue
        if in_block:
            if stripped and not line[:1].isspace() and stripped.endswith(":") and not stripped.startswith("token"):
                in_block = False
                continue
            if stripped.startswith("token:"):
                return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def _as_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes"):
            return True
        if lowered in ("0", "false", "no"):
            return False
    return None


def _coerce_playout(data: dict, path: str) -> dict:
    bools = ("on", "mic") if path == "/voiceover" else ("on",)
    floats = (
        ("fade", "remaining", "gain")
        if path == "/voiceover"
        else (
            "silence_s",
            "rms_db",
            "rms",
            "gain",
            "harbor_min",
            "harbor_max",
            "harbor_s",
            "rss_mb",
            "cpu_pct",
        )
    )
    for key in bools:
        parsed = _as_bool(data.get(key))
        if parsed is not None:
            data[key] = parsed
    for key in floats:
        if key not in data:
            continue
        parsed = _as_float(data.get(key))
        if parsed is not None:
            data[key] = parsed
    return data


def _playout_json(raw: bytes, path: str) -> bytes:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    if not isinstance(data, dict):
        return raw
    return json.dumps(_coerce_playout(data, path), ensure_ascii=False).encode("utf-8")


def yaml_harbor() -> tuple[float, float]:
    min_s, max_s = 5.0, 30.0
    if not PLAYOUT_YAML.exists():
        return min_s, max_s
    in_block = False
    for raw in PLAYOUT_YAML.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0]
        stripped = line.strip()
        if stripped.startswith("playout_process_config"):
            in_block = True
            continue
        if in_block:
            if stripped and not line[:1].isspace() and stripped.endswith(":") and not stripped.startswith(
                "input_"
            ):
                in_block = False
                continue
            if stripped.startswith("input_min_buffer:"):
                parsed = _as_float(stripped.split(":", 1)[1].strip().strip("\"'"))
                if parsed is not None:
                    min_s = parsed
            elif stripped.startswith("input_max_buffer:"):
                parsed = _as_float(stripped.split(":", 1)[1].strip().strip("\"'"))
                if parsed is not None:
                    max_s = parsed
    return min_s, max_s


def media_root() -> Path | None:
    for raw in MEDIA_CANDIDATES:
        if not raw:
            continue
        path = Path(raw)
        if path.is_dir():
            return path
    return None


def disk_pct(path: Path) -> float | None:
    try:
        stat = os.statvfs(path)
    except OSError:
        return None
    total = stat.f_frsize * stat.f_blocks
    free = stat.f_frsize * stat.f_bavail
    if total <= 0:
        return None
    return round(100.0 * (1.0 - free / total), 1)


def machine_payload() -> tuple[int, bytes]:
    status, raw = playout_http("GET", "/health", require_token=False)
    health: dict = {}
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
        if isinstance(parsed, dict):
            health = _coerce_playout(parsed, "/health")
    except (UnicodeDecodeError, json.JSONDecodeError):
        health = {"error": "playout"}
    min_s, max_s = yaml_harbor()
    if _as_float(health.get("harbor_min")) is None:
        health["harbor_min"] = min_s
    if _as_float(health.get("harbor_max")) is None:
        health["harbor_max"] = max_s
    root = media_root()
    health["disk_pct"] = disk_pct(root) if root else None
    health["disk_path"] = str(root) if root else ""
    if "status" not in health:
        health["status"] = "down" if status >= 400 else "live"
    return (200 if status < 500 else status), json.dumps(health, ensure_ascii=False).encode("utf-8")


def playout_http(
    method: str, path: str, body: bytes | None = None, require_token: bool = True
) -> tuple[int, bytes]:
    token = playout_token()
    if require_token and not token:
        return 503, b'{"error":"playout token"}'
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    req = Request(f"{PLAYOUT_URL}{path}", data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=4) as res:
            raw = res.read()
            return res.status, _playout_json(raw, path) if path in ("/voiceover", "/health") else raw
    except HTTPError as exc:
        raw = exc.read()
        return int(exc.code), _playout_json(raw, path) if path in ("/voiceover", "/health") else raw
    except URLError as exc:
        return 502, json.dumps({"error": str(exc.reason)}).encode()


def _pcm_host_port() -> tuple[str, int]:
    raw = PLAYOUT_PCM
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    host, _, port = raw.partition(":")
    return host or "radiotomate-playout", int(port or "6802")


def _ws_accept(key: str) -> str:
    digest = hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_send(wfile, opcode: int, data: bytes = b"") -> None:
    header = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + n.to_bytes(2, "big")
    else:
        header += bytes([127]) + n.to_bytes(8, "big")
    wfile.write(header + data)
    wfile.flush()


def _ws_recv(rfile) -> tuple[int, bytes] | None:
    hdr = rfile.read(2)
    if len(hdr) < 2:
        return None
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    length = hdr[1] & 0x7F
    if length == 126:
        ext = rfile.read(2)
        if len(ext) < 2:
            return None
        length = int.from_bytes(ext, "big")
    elif length == 127:
        ext = rfile.read(8)
        if len(ext) < 8:
            return None
        length = int.from_bytes(ext, "big")
    mask = rfile.read(4) if masked else b""
    data = rfile.read(length) if length else b""
    if len(data) < length:
        return None
    if masked and mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


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
        if path == "/voiceover/pcm":
            self._voiceover_pcm()
            return
        if path == "/voiceover":
            status, body = playout_http("GET", "/voiceover")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/machine.json":
            status, body = machine_payload()
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        if path == "/voiceover":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            status, body = playout_http("POST", "/voiceover", raw)
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
        if not isinstance(data, dict):
            self.send_error(400)
            return
        with LOCK:
            save_state(apply_hook(load_state(), data))
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _voiceover_pcm(self) -> None:
        if (self.headers.get("Upgrade") or "").lower() != "websocket":
            self.send_error(426)
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400)
            return
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept(key))
        self.end_headers()
        sock: socket.socket | None = None
        host, port = _pcm_host_port()
        try:
            while True:
                frame = _ws_recv(self.rfile)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 8:
                    break
                if opcode == 9:
                    _ws_send(self.wfile, 10, data)
                    continue
                if opcode not in (1, 2) or not data:
                    continue
                if sock is None:
                    sock = socket.create_connection((host, port), timeout=2)
                    sock.settimeout(None)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(data)
        except Exception as exc:
            print(f"[nowplaying] voiceover pcm: {exc}")
        finally:
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[nowplaying] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    if not STATE.exists():
        save_state(dict(MOCK))
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Now Playing on http://127.0.0.1:{PORT}/now.json")
    httpd.serve_forever()
