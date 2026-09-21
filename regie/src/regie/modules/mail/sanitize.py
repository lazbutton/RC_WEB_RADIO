from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.I)
SECRET_KEYS = re.compile(
    r"^(token|key|auth|reset|confirm|password|passwd|invite|secret|access_token|id_token|session)$",
    re.I,
)
AUTH_PATH = re.compile(
    r"/(wp-login|wp-admin|login|signin|sign-in|reset|password|passwd|auth|confirm|invite)(/|$)",
    re.I,
)
TRACKING_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "c2id",
    "msclkid",
    "igshid",
}
REMOVED = "[lien retiré]"
TOKENISH = re.compile(r"^[A-Za-z0-9_\-.=+/]{24,}$")
IMAGE_PATH = re.compile(r"\.(?:png|gif|jpe?g|svg|webp)(?:$|\?)", re.I)


def _drop_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    path = (parts.path or "").lower()
    if AUTH_PATH.search(path):
        return True
    if "wp-login.php" in path:
        return True
    query = parse_qsl(parts.query, keep_blank_values=True)
    for key, value in query:
        if SECRET_KEYS.match(key or ""):
            return True
        if TOKENISH.match(value or ""):
            return True
    return False


def scrub_url(url: str) -> str | None:
    raw = (url or "").strip().rstrip(".,;:!?")
    if not raw:
        return None
    if _drop_url(raw):
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_KEYS]
    query = urlencode(kept, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def text(blob: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            path = urlsplit(raw).path or ""
        except ValueError:
            return REMOVED
        if IMAGE_PATH.search(path):
            return ""
        cleaned = scrub_url(raw)
        return cleaned if cleaned else REMOVED

    return URL_RE.sub(repl, blob or "")
