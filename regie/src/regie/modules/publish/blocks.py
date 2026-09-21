"""Famille de blocs iframe sur un contrat unique : `?embed=1`, postMessage hauteur/scroll, liens `target=_top`, CSP stricte.

Chaque bloc est un HTML autonome (pas de JS externe) qui lit son JSON voisin. Le JSON est régénéré par Régie.
"""

from __future__ import annotations

import html
import json
from typing import Any

EMBED_JS = """
(function(){
  var embed = new URLSearchParams(location.search).get('embed') === '1' || window.self !== window.top;
  if (embed) document.documentElement.classList.add('embed');
  function post(){ if (window.parent === window) return; try { window.parent.postMessage({type:'regie:height', block: document.body.dataset.block, height: document.documentElement.scrollHeight}, '*'); } catch (e) {} }
  window.addEventListener('load', post); window.addEventListener('resize', post);
  if (window.ResizeObserver) new ResizeObserver(post).observe(document.body);
  window.addEventListener('message', function(ev){ if (ev.data && ev.data.type === 'regie:scroll') window.scrollTo(0, Number(ev.data.top) || 0); });
  document.addEventListener('click', function(ev){ var a = ev.target.closest && ev.target.closest('a[href]'); if (a && embed) a.target = '_top'; });
})();
"""

CSS = """
:root{--ink:#111;--muted:#666;--hair:#e6e6e6;--bg:#fff;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);background:var(--bg)}
*{box-sizing:border-box}body{margin:0;padding:16px}html.embed body{padding:0}
h1{font-size:15px;letter-spacing:.02em;text-transform:uppercase;margin:0 0 12px}html.embed h1{display:none}
ul{list-style:none;margin:0;padding:0}li{display:grid;grid-template-columns:72px 1fr;gap:12px;padding:10px 0;border-top:1px solid var(--hair)}li:first-child{border-top:0}
time{font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px}
a{color:inherit;text-decoration:none}a:hover{text-decoration:underline}
.t{font-weight:600}.s{color:var(--muted);font-size:13px}.empty{color:var(--muted);padding:12px 0}
audio{width:100%;margin-top:6px}.tag{display:inline-block;border:1px solid var(--hair);border-radius:999px;padding:1px 8px;font-size:11px;margin-right:6px}
.credit{margin-top:12px;font-size:11px;color:var(--muted)}
"""

CSP_META = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https: data:; media-src https: http:; connect-src 'self' https:; base-uri 'none'; form-action 'none'"


def page(block: str, title: str, body: str, data: Any) -> str:
    return (
        "<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{CSP_META}\"><title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body data-block=\"{html.escape(block)}\"><h1>{html.escape(title)}</h1>{body}"
        f"<script type=\"application/json\" id=\"data\">{json.dumps(data, ensure_ascii=False).replace('</', '<\\/')}</script>"
        f"<script>{EMBED_JS}</script></body></html>"
    )


def _when(value: str) -> str:
    if not value:
        return ""
    day, _, rest = str(value).partition("T")
    return f"{day[8:10]}/{day[5:7]} {rest[:5]}".strip()


def agenda_block(events: list[dict[str, Any]]) -> str:
    items = []
    for event in events:
        link = html.escape(event.get("url") or "#")
        items.append(f"<li><time>{_when(event.get('starts_at') or '')}</time><div><a href=\"{link}\" class=\"t\">{html.escape(event.get('title') or '')}</a><div class=\"s\">{html.escape(' · '.join(p for p in [event.get('venue_name') or '', event.get('price') or ''] if p))}</div></div></li>")
    body = f"<ul>{''.join(items)}</ul>" if items else "<p class=\"empty\">Rien de prévu pour l'instant.</p>"
    return page("agenda", "Agenda Radio Campus", body + "<p class=\"credit\">Données Outlive · Radio Campus Orléans</p>", events)


def upcoming_block(slots: list[dict[str, Any]]) -> str:
    items = []
    for slot in slots:
        items.append(f"<li><time>{html.escape(slot.get('when') or '')}</time><div><span class=\"t\">{html.escape(slot.get('title') or '')}</span><div class=\"s\">{html.escape(slot.get('subtitle') or '')}</div></div></li>")
    body = f"<ul>{''.join(items)}</ul>" if items else "<p class=\"empty\">Grille en préparation.</p>"
    return page("upcoming", "Prochainement à l'antenne", body, slots)


def podcasts_block(podcasts: list[dict[str, Any]]) -> str:
    items = []
    for podcast in podcasts:
        audio = f"<audio controls preload=\"none\" src=\"{html.escape(podcast.get('audio_url') or '')}\"></audio>" if podcast.get("audio_url") else ""
        items.append(f"<li><time>{html.escape((podcast.get('published_at') or '')[:10])}</time><div><span class=\"tag\">{html.escape(podcast.get('show') or '')}</span><a href=\"{html.escape(podcast.get('url') or '#')}\" class=\"t\">{html.escape(podcast.get('title') or '')}</a><div class=\"s\">{html.escape((podcast.get('description') or '')[:160])}</div>{audio}</div></li>")
    body = f"<ul>{''.join(items)}</ul>" if items else "<p class=\"empty\">Pas encore de podcast.</p>"
    return page("podcasts", "Derniers podcasts", body, podcasts)


def playlist_block(tracks: list[dict[str, Any]]) -> str:
    items = [f"<li><time>{html.escape(str(t.get('week') or ''))}</time><div><span class=\"t\">{html.escape(t.get('artist') or '')}</span><div class=\"s\">{html.escape(t.get('title') or '')}</div></div></li>" for t in tracks]
    body = f"<ul>{''.join(items)}</ul>" if items else "<p class=\"empty\">Playlist à venir.</p>"
    return page("playlist", "Playlist de la semaine", body, tracks)


def team_block(people: list[dict[str, Any]]) -> str:
    items = [f"<li><time></time><div><span class=\"t\">{html.escape(p.get('name') or '')}</span><div class=\"s\">{html.escape(p.get('role') or '')}</div></div></li>" for p in people]
    body = f"<ul>{''.join(items)}</ul>" if items else "<p class=\"empty\">Équipe en cours de présentation.</p>"
    return page("team", "L'équipe", body + "<p class=\"credit\">Contact : contact@orleans.radiocampus.org</p>", people)


def embed_snippet(base_url: str, block: str, height: int = 480) -> str:
    src = f"{base_url.rstrip('/')}/{block}/index.html?embed=1"
    return (
        f"<iframe src=\"{src}\" title=\"Radio Campus · {block}\" style=\"width:100%;border:0;height:{height}px\" loading=\"lazy\" id=\"regie-{block}\"></iframe>"
        f"<script>window.addEventListener('message',function(e){{if(e.data&&e.data.type==='regie:height'&&e.data.block==='{block}'){{document.getElementById('regie-{block}').style.height=e.data.height+'px'}}}});</script>"
    )
