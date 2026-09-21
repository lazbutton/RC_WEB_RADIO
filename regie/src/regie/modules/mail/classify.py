from __future__ import annotations

import base64
import json
import re
from html import unescape
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from regie.modules.mail.sanitize import scrub_url, text as sanitize_text
from regie.modules.mail.threads import forward_context, short_summary, visible_body

CATEGORIES = ("todo", "waiting", "read", "newsletters", "spam")

CATEGORY_LABELS = {
    "todo": "À faire",
    "waiting": "En attente",
    "read": "À lire",
    "newsletters": "Newsletters",
    "spam": "Spam probable",
}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

HAIKU_BATCH_SIZE = 10
HAIKU_EXCERPT = 600
SONNET_EXCERPT = 2000
SUMMARY_MAX = 1200
AUTO_THRESHOLDS = {"newsletters": 0.72, "read": 0.80, "spam": 0.88}
ACTION_HINTS = ("facture", "invoice", "deadline", "asap", "merci de", "peux-tu", "pouvez-vous")
SPAM_BITS = ("viagra", "crypto", "prize", "congratulations", "click here")

SYSTEM_PROMPT = """Tu classes un e-mail de radio associative (Radio Campus Orléans).
Réponds UNIQUEMENT en JSON compact, sans markdown :
{"category":"todo|waiting|read|newsletters|spam","reason":"phrase courte en français","summary":"3 à 6 phrases en français","confidence":0.0}

Équipe : cette boîte = volontaire musique (toi). Lou = l'autre volontaire. Erwann = responsable. Viviane = antenne / invitée. Lou et toi vous forwardz des fils Viviane/Erwann : ce n'est pas « Viviane t'écrit ».

Règles :
- todo : action demandée, deadline, facture, accès, signature, réponse attendue de TOI
- waiting : on attend une réponse de quelqu'un d'autre, ou un suivi déjà lancé
- read : info utile à lire, pas d'action immédiate ; transfert / idée invitée / pour info sans question
- newsletters : listes, digest, promo, List-Unsubscribe, mailing de masse
- spam : phishing, pub froide, hors sujet, piège à clics
- summary : qui t'écrit (Lou, Erwann, Viviane…), avec qui dans le fil transféré, de quoi, date si présente, ce qu'on attend de toi ou « pour info ». Jamais recopier « Bonjour ».
- Si le mail contient un site, page artiste, Bandcamp, festival, dossier de presse : recopie ces URLs dans le summary. N'invente aucun lien. Pas de désinscription ni tracking.
Ne jamais inventer de faits absents du message. Extraite seulement, pas les pièces jointes.
"""

HAIKU_SYSTEM = """Tu classes des e-mails de radio associative (Radio Campus Orléans).
Réponds UNIQUEMENT un JSON array, sans markdown, un objet par mail, même ordre :
[{"i":0,"category":"todo|waiting|read|newsletters|spam","reason":"phrase FR","summary":"3 à 6 phrases","confidence":0.0}]

Équipe : toi = volontaire musique. Lou = l'autre volontaire. Erwann = responsable. Viviane = antenne. Un forward Lou n'est pas un mail de Viviane.

Règles :
- todo : action, deadline, facture, accès, signature, réponse attendue de toi
- waiting : on attend quelqu'un d'autre
- read : info utile, pas d'action ; transfert / idée invitée sans question
- newsletters : listes, digest, promo, mailing
- spam : phishing, pub froide, piège à clics
- summary : qui, avec qui, quoi, date si présente, attente ou pour info. Pas de « Bonjour ».
- Recopie les URLs utiles (site, page artiste, dossier). N'invente aucun lien.
N'invente rien. Extraite seulement.
"""

DRAFT_PROMPT = """Tu rédiges un brouillon de réponse pour un mail de radio associative (Radio Campus).
Règles :
- Français, prêt à coller dans Roundcube. Pas d'objet, pas de markdown.
- Reprends tutoiement ou vouvoiement selon le mail reçu.
- Ton simple, concret, pas corporate.
- N'invente aucun fait, date, montant ou engagement absent du mail.
- Si une info manque, pose une question courte.
- Ne signe pas. Pas de « Cordialement » ni de nom : une signature sera ajoutée ensuite.
- Si Lou t'a forward un fil : tu réponds à Lou, pas à Viviane à sa place.
"""

BRIEF_PROMPT = """Tu fais un récap, et seulement si besoin un brouillon, pour Radio Campus Orléans.
Réponds UNIQUEMENT en JSON, sans markdown :
{"summary":"3 à 6 phrases en français","draft":""}

Équipe :
- Toi : volontaire musique, destinataire de cette boîte.
- Lou : l'autre volontaire (locale). Vous vous forwardz des échanges.
- Erwann : votre responsable.
- Viviane : souvent antenne / invitée / programmation.

Règles :
- Qui t'écrit = l'expéditeur du mail vers cette boîte, pas une personne citée ou @mentionnée.
- Si Lou ou Erwann transfère un fil Viviane : « Lou te passe un échange Viviane ↔ X ». Jamais « Viviane t'écrit » dans ce cas.
- Si l'expéditeur est Viviane : « Viviane t'écrit / te passe … », même si Lou est en copie ou @mentionnée.
- Date si elle est dans le mail. Ce qu'on attend de toi, ou pour info. Pas de « Bonjour ». N'invente rien.
- Aucun fait hors du fil (IP, e-mail, nombre, nom de fichier). Jamais « pièce absente » si la liste MIME en a.
- Pour préparer une interview / chercher l'artiste / le lieu : si le mail a des URLs utiles (site officiel, Bandcamp, page artiste, festival, dossier de presse), recopie-les à la fin du summary, une par ligne, telles quelles. N'invente aucun lien. Pas de désinscription, tracking, image, ni « voir dans le navigateur ».
- Si le mail a des pièces jointes, cite leurs noms. N'invente pas le contenu d'un PDF. Jamais de binaire.
- draft vide si : pour info, idée invitée, déjà calé, CC, newsletter, ou si Lou/Erwann ne te demandent rien.
- draft non vide seulement si TOI tu dois répondre. En général à Lou ou Erwann (celui qui a transféré), pas à Viviane à la place de Lou, sauf si le mail dit d'écrire à Viviane.
- Corps à coller dans Roundcube : pas d'objet, pas de markdown, pas de signature, pas de Cordialement. Tutoiement selon le mail. Ne signe pas pour Lou. Tu n'es pas le responsable.
"""


def clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "…"


URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.I)
A_HREF_RE = re.compile(
    r'(?is)<a\b[^>]*\bhref=["\'](https?://[^"\']+)["\'][^>]*>(.*?)</a>'
)
JUNK_HOSTS = (
    "sentry.io",
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "t.co",
)
JUNK_HOST_BITS = (
    "list-manage.com",
    "sendgrid.net",
    "sparkpostmail.com",
    "mandrillapp.com",
    "mailgun.org",
    "click.mlsend.com",
    "e2ma.net",
    "mjt.lu",
    "mailinblue.com",
    "sendibt3.com",
)
WRAP_HOST_BITS = (
    "mjt.lu",
    "mailinblue.com",
    "sendibt3.com",
    "mailchi.mp",
)
JUNK_PATH = (
    "unsubscribe",
    "optout",
    "opt-out",
    "opt_out",
    "email-preferences",
    "view-in-browser",
    "viewinbrowser",
    "about:blank",
)
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff", ".woff2")


def _clean_url(raw: str) -> str:
    return (raw or "").strip().rstrip(".,;:!?")


def _url_host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_junk_link(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = _url_host(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    blob = f"{path}?{query}"
    if any(host == junk or host.endswith("." + junk) for junk in JUNK_HOSTS):
        return True
    if any(bit in host for bit in JUNK_HOST_BITS):
        return True
    if any(bit in blob for bit in JUNK_PATH):
        return True
    if path.endswith(SKIP_EXT):
        return True
    if host in {"facebook.com", "fb.com"} and (
        "/l.php" in path or path.startswith("/tr") or "/sharer" in path
    ):
        return True
    if host in {"instagram.com", "www.instagram.com"} and path in {"", "/"}:
        return True
    return False


def _b64_url(raw: str) -> str | None:
    token = (raw or "").strip()
    if not token or len(token) < 16:
        return None
    padded = token + "=" * ((4 - len(token) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "ignore")
    except Exception:
        return None
    if decoded.startswith("http://") or decoded.startswith("https://"):
        return decoded
    return None


def unwrap_url(url: str) -> str:
    current = _clean_url(url)
    host = _url_host(current)
    if any(bit in host for bit in WRAP_HOST_BITS):
        parsed = urlparse(current)
        for piece in reversed((parsed.path or "").rstrip("/").split("/")):
            found = _b64_url(unquote(piece))
            if found:
                return found
        query = unquote(parsed.query or "")
        for match in URL_RE.findall(query):
            if match and _url_host(match) and not any(bit in _url_host(match) for bit in WRAP_HOST_BITS):
                return match
    return current


def _link_key(url: str) -> str:
    parsed = urlparse(url)
    host = _url_host(url)
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}"


def useful_links(text: str, limit: int = 5) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in URL_RE.findall(text or ""):
        url = unwrap_url(raw)
        if not url or any(bit in _url_host(url) for bit in WRAP_HOST_BITS):
            continue
        if _is_junk_link(url):
            continue
        cleaned = scrub_url(url)
        if not cleaned:
            continue
        url = cleaned
        key = _link_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(url)
        if len(found) >= limit:
            break
    return found


def with_research_links(summary: str, source: str) -> str:
    recap = (summary or "").strip()
    links = useful_links(source)
    if not links:
        return recap[:SUMMARY_MAX]
    present = recap.lower()
    extra = [url for url in links if url.lower() not in present and _link_key(url) not in present]
    if not extra:
        return recap[:SUMMARY_MAX]
    return (recap + "\n\n" + "\n".join(extra)).strip()[:SUMMARY_MAX]


def usage_line(stats: dict[str, Any] | None) -> str:
    stats = stats or {}
    if not any(stats.get(key) for key in ("haiku_calls", "sonnet_calls")):
        return ""
    return (
        f"Scan {int(stats.get('haiku_calls') or 0)} · "
        f"Récap {int(stats.get('sonnet_calls') or 0)}"
    )


def can_auto_move(category: str, confidence: float, auto_mode: bool) -> bool:
    if not auto_mode:
        return False
    if category in {"todo", "waiting"}:
        return False
    return float(confidence or 0) >= AUTO_THRESHOLDS.get(category, 1.01)


def should_escalate(result: dict[str, Any], subject: str = "", excerpt: str = "") -> bool:
    category = str(result.get("category") or "")
    if category in {"todo", "waiting"}:
        return True
    if float(result.get("confidence") or 0) < AUTO_THRESHOLDS.get(category, 0.8):
        return True
    blob = f"{subject} {excerpt}".lower()
    if category not in {"todo", "waiting"} and any(word in blob for word in ACTION_HINTS):
        return True
    return False


def _anchor_text(match: re.Match[str]) -> str:
    href = unwrap_url(unescape(match.group(1)))
    inner = re.sub(r"(?s)<[^>]+>", " ", match.group(2))
    inner = unescape(re.sub(r"\s+", " ", inner)).strip()
    if inner and href.lower() not in inner.lower():
        return f"{inner} {href}"
    return inner or href


def strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = A_HREF_RE.sub(_anchor_text, text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def excerpt_body(text: str | None, html: str | None, limit: int) -> str:
    body = (text or "").strip() or strip_html(html or "")
    cleaned = visible_body(body)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return sanitize_text(clip(cleaned.strip(), limit))


def parse_classification(raw: str) -> dict[str, Any]:
    blob = raw.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?", "", blob).strip()
        blob = re.sub(r"```$", "", blob).strip()
    match = re.search(r"\{.*\}", blob, re.S)
    if not match:
        raise ValueError("réponse IA sans JSON")
    data = json.loads(match.group(0))
    category = str(data.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        raise ValueError(f"catégorie inconnue: {category}")
    confidence = data.get("confidence", 0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(data.get("reason") or "").strip()[:280]
    summary = str(data.get("summary") or "").strip()[:SUMMARY_MAX]
    return {"category": category, "reason": reason, "summary": summary, "confidence": confidence}


def parse_classification_batch(raw: str, expected: int) -> list[dict[str, Any]]:
    blob = raw.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?", "", blob).strip()
        blob = re.sub(r"```$", "", blob).strip()
    match = re.search(r"\[.*\]", blob, re.S)
    if not match:
        raise ValueError("réponse IA sans JSON array")
    data = json.loads(match.group(0))
    if not isinstance(data, list) or not data:
        raise ValueError("lot IA vide")
    by_index: dict[int, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            continue
        parsed = parse_classification(json.dumps(row, ensure_ascii=False))
        try:
            pos = int(row.get("i", idx))
        except (TypeError, ValueError):
            pos = idx
        by_index[pos] = parsed
        ordered.append(parsed)
    out: list[dict[str, Any]] = []
    for idx in range(expected):
        if idx in by_index:
            out.append(by_index[idx])
        elif idx < len(ordered):
            out.append(ordered[idx])
        else:
            raise ValueError("lot IA incomplet")
    return out


def _is_list_mail(headers: dict[str, str], blob: str) -> bool:
    precedence = (headers.get("precedence") or "").lower()
    if headers.get("list-unsubscribe") or headers.get("list-id"):
        return True
    if precedence in {"bulk", "list"}:
        return True
    if "newsletter" in blob and "unsubscribe" in blob:
        return True
    return False


def heuristic_gate(
    sender: str,
    subject: str,
    excerpt: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    blob = f"{sender} {subject} {excerpt}".lower()
    if _is_list_mail(headers, blob):
        return {
            "category": "newsletters",
            "reason": "Liste ou désinscription détectée (heuristique, sans IA).",
            "summary": short_summary(excerpt, "Newsletter ou liste de diffusion."),
            "confidence": 0.78,
            "via": "heuristic",
        }
    if any(bit in blob for bit in SPAM_BITS):
        return {
            "category": "spam",
            "reason": "Motifs spam évidents (heuristique, sans IA).",
            "summary": short_summary(excerpt, "Message suspect."),
            "confidence": 0.9,
            "via": "heuristic",
        }
    return None


def heuristic_classification(
    sender: str,
    subject: str,
    excerpt: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    gated = heuristic_gate(sender, subject, excerpt, headers)
    if gated:
        gated["summary"] = with_research_links(gated.get("summary") or "", excerpt)
        return gated
    blob = f"{sender} {subject} {excerpt}".lower()
    if any(word in blob for word in ACTION_HINTS):
        return {
            "category": "todo",
            "reason": "Demande d'action probable (heuristique, sans IA).",
            "summary": with_research_links(short_summary(excerpt, "Une action est demandée."), excerpt),
            "confidence": 0.4,
            "via": "heuristic",
        }
    return {
        "category": "read",
        "reason": "Pas d'indice fort — à lire (heuristique, sans IA).",
        "summary": with_research_links(short_summary(excerpt, "À lire, sans action claire."), excerpt),
        "confidence": 0.3,
        "via": "heuristic",
    }


def anthropic_headers(api_key: str, workspace_id: str = "") -> dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    workspace = workspace_id.strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return headers


def anthropic_error_message(response: httpx.Response) -> str:
    message = (response.text or "").strip()[:400]
    try:
        payload = response.json()
        err = payload.get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or message)
        elif isinstance(err, str):
            message = err
    except Exception:
        pass
    lowered = message.lower()
    if "not scoped to a workspace" in lowered or "anthropic-workspace-id" in lowered:
        return (
            "Clé Anthropic multi-workspace : ajoute ANTHROPIC_WORKSPACE_ID "
            "(console Anthropic → Settings → Workspaces, id wrkspc_…) "
            "ou crée une clé liée à un workspace."
        )
    if "credit balance is too low" in lowered or "purchase credits" in lowered:
        return (
            "Compte Anthropic sans crédits : Plans & Billing sur console.anthropic.com, "
            "puis relance un scan."
        )
    return f"Anthropic HTTP {response.status_code}: {message}"[:400]


def _read_usage(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }


def _anthropic_text(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float,
    workspace_id: str = "",
    cache_system: bool = True,
    effort: str = "",
) -> tuple[str, dict[str, int]]:
    system_payload: Any = system
    if cache_system:
        system_payload = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_payload,
        "messages": [{"role": "user", "content": user}],
    }
    level = (effort or "").strip().lower()
    if level:
        body["output_config"] = {"effort": level}
    else:
        # Sonnet 4.5 still accepts temperature. Opus 5 rejects anything but 1.0.
        body["temperature"] = 0
    response = httpx.post(
        ANTHROPIC_URL,
        headers=anthropic_headers(api_key, workspace_id),
        json=body,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(anthropic_error_message(response))
    payload = response.json()
    blocks = payload.get("content") or []
    text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
    if not text.strip():
        raise ValueError("réponse Anthropic vide")
    return text, _read_usage(payload)


def classify_with_anthropic(
    *,
    api_key: str,
    model: str,
    sender: str,
    subject: str,
    excerpt: str,
    extra_prompt: str = "",
    workspace_id: str = "",
    timeout: float = 45.0,
) -> dict[str, Any]:
    payload = {"from": sender, "subject": subject, "body": clip(excerpt, SONNET_EXCERPT)}
    system = SYSTEM_PROMPT
    if extra_prompt.strip():
        system += "\n\nConsignes en plus :\n" + extra_prompt.strip()[:2000]
    raw, usage = _anthropic_text(
        api_key=api_key,
        model=model,
        system=system,
        user=json.dumps(payload, ensure_ascii=False),
        max_tokens=500,
        timeout=timeout,
        workspace_id=workspace_id,
    )
    out = parse_classification(raw)
    out["summary"] = with_research_links(out.get("summary") or "", excerpt)
    out["via"] = "sonnet"
    out["_usage"] = usage
    return out


def classify_batch_haiku(
    *,
    api_key: str,
    model: str,
    mails: list[dict[str, str]],
    extra_prompt: str = "",
    workspace_id: str = "",
    timeout: float = 45.0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not mails:
        return [], {"input_tokens": 0, "output_tokens": 0}
    packed = []
    for index, mail in enumerate(mails):
        packed.append(
            {
                "i": index,
                "from": mail.get("from") or mail.get("sender") or "",
                "subject": mail.get("subject") or "",
                "body": clip(mail.get("excerpt") or mail.get("body") or "", SONNET_EXCERPT),
            }
        )
    system = HAIKU_SYSTEM
    if extra_prompt.strip():
        system += "\n\nConsignes :\n" + extra_prompt.strip()[:400]
    raw, usage = _anthropic_text(
        api_key=api_key,
        model=model,
        system=system,
        user=json.dumps({"mails": packed}, ensure_ascii=False),
        max_tokens=min(400 * len(packed), 4000),
        timeout=timeout,
        workspace_id=workspace_id,
    )
    parsed = parse_classification_batch(raw, len(packed))
    for row, mail in zip(parsed, mails, strict=True):
        row["via"] = "haiku"
        row["summary"] = with_research_links(
            row.get("summary") or "",
            mail.get("excerpt") or mail.get("body") or "",
        )
    return parsed, usage


def draft_reply_with_anthropic(
    *,
    api_key: str,
    model: str,
    sender: str,
    subject: str,
    excerpt: str,
    extra_prompt: str = "",
    workspace_id: str = "",
    timeout: float = 60.0,
) -> tuple[str, dict[str, int]]:
    payload = {"from": sender, "subject": subject, "body": clip(excerpt, SONNET_EXCERPT)}
    system = DRAFT_PROMPT
    if extra_prompt.strip():
        system += "\n\nConsignes en plus :\n" + extra_prompt.strip()[:2000]
    raw, usage = _anthropic_text(
        api_key=api_key,
        model=model,
        system=system,
        user=json.dumps(payload, ensure_ascii=False),
        max_tokens=700,
        timeout=timeout,
        workspace_id=workspace_id,
    )
    return raw.strip()[:4000], usage


def parse_brief(raw: str) -> dict[str, str]:
    blob = raw.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?", "", blob).strip()
        blob = re.sub(r"```$", "", blob).strip()
    match = re.search(r"\{.*\}", blob, re.S)
    if not match:
        raise ValueError("récap IA sans JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("récap IA invalide")
    summary = str(data.get("summary") or "").strip()[:SUMMARY_MAX]
    draft = str(data.get("draft") or "").strip()[:4000]
    if draft.lower() in {"null", "none", "n/a", "-", "n.a."}:
        draft = ""
    return {"summary": summary, "draft": draft}


def brief_payload(sender: str, subject: str, excerpt: str, attachments: Any = None) -> dict[str, Any]:
    from regie.modules.mail.attachments import names, parse_list

    ctx = forward_context(excerpt, subject)
    links = useful_links(excerpt or "")
    if isinstance(attachments, list) and attachments and isinstance(attachments[0], str):
        files = [str(name)[:120] for name in attachments[:12]]
    else:
        files = names(parse_list(attachments))
    return {"from": sender or "", "subject": subject or "", **ctx, "links": links, "attachments": files}


def brief_with_anthropic(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    subject: str,
    extra_prompt: str = "",
    workspace_id: str = "",
    timeout: float = 120.0,
    effort: str = "high",
) -> tuple[dict[str, str], dict[str, int]]:
    packed = []
    sources: list[str] = []
    for row in messages[-8:]:
        excerpt = str(row.get("excerpt") or "")
        note = str(row.get("note") or "")
        quoted = str(row.get("quoted") or "")
        links = list(row.get("links") or useful_links("\n".join([excerpt, note, quoted])))
        files = [str(name)[:120] for name in list(row.get("attachments") or [])[:12]]
        packed.append(
            {
                "from": row.get("from") or row.get("sender") or "",
                "note": clip(note, 800),
                "participants": list(row.get("participants") or []),
                "quoted": clip(quoted, SONNET_EXCERPT),
                "links": links,
                "attachments": files,
            }
        )
        sources.extend([excerpt, note, quoted, "\n".join(links)])
    system = BRIEF_PROMPT
    if extra_prompt.strip():
        system += "\n\nConsignes en plus :\n" + extra_prompt.strip()[:2000]
    raw, usage = _anthropic_text(
        api_key=api_key,
        model=model,
        system=system,
        user=json.dumps({"subject": subject, "messages": packed}, ensure_ascii=False),
        max_tokens=8192,
        timeout=timeout,
        workspace_id=workspace_id,
        effort=effort,
    )
    out = parse_brief(raw)
    out["summary"] = with_research_links(out.get("summary") or "", "\n".join(sources))
    return out, usage
