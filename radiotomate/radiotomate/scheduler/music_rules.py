"""Auto-DJ selection rules: artist/title separation against the as-run log.
Pure functions plus one read-only query; no sequencer state."""

from __future__ import annotations

from datetime import datetime, timedelta
from random import choice
from typing import TYPE_CHECKING

from sqlalchemy import select

from radiotomate.models import MetadataLog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration
    from radiotomate.models import ClockPosition

ARTIST_WINDOW = timedelta(minutes=60)
ARTIST_LAST_N = 3
TITLE_LAST_N = 20


async def _pick_music(  # noqa: PLR0913
    session: ormSession,
    beets: BeetsIntegration,
    pos: ClockPosition,
    now: datetime,
    extra_artists: set[str] | None = None,
    extra_titles: set[str] | None = None,
) -> object | None:
    category = pos.category
    queries: list[str] = []
    if category is not None:
        queries.append(category.query)
        if category.empty_query:
            queries.append(category.empty_query)
    queries.append("")
    history = await _music_history(session)
    forbidden_artists, forbidden_titles = _rule_sets(history, now)
    if extra_artists:
        forbidden_artists = forbidden_artists | extra_artists
    if extra_titles:
        forbidden_titles = forbidden_titles | extra_titles
    for query in queries:
        items = await beets.search(query)
        picked = _choose_item(items, forbidden_artists, forbidden_titles)
        if picked is not None:
            return picked
    return None


def _choose_item(items, forbidden_artists: set[str], forbidden_titles: set[str]):
    if not items:
        return None
    filtered = [
        item
        for item in items
        if str(getattr(item, "artist", "") or "") not in forbidden_artists
        and str(getattr(item, "title", "") or "") not in forbidden_titles
    ]
    if filtered:
        return choice(filtered)
    # Prefer another title over repeating one still in the history window.
    other_titles = [
        item
        for item in items
        if str(getattr(item, "title", "") or "") not in forbidden_titles
    ]
    if other_titles:
        return choice(other_titles)
    return choice(list(items))


async def _music_history(session: ormSession) -> list[MetadataLog]:
    q = (
        select(MetadataLog)
        .where(MetadataLog.cart_id.is_(None))
        .order_by(MetadataLog.on_air.desc())
        .limit(max(TITLE_LAST_N, ARTIST_LAST_N) + 5)
    )
    return list(await session.scalars(q))


def _rule_sets(
    logs: list[MetadataLog],
    now: datetime,
) -> tuple[set[str], set[str]]:
    naive = now.replace(tzinfo=None) if now.tzinfo else now
    last_artists = [log.artist for log in logs[:ARTIST_LAST_N] if log.artist]
    window_artists = []
    for log in logs:
        if not log.artist or log.on_air is None:
            continue
        on_air = log.on_air
        if on_air.tzinfo:
            on_air = on_air.replace(tzinfo=None)
        if naive - on_air <= ARTIST_WINDOW:
            window_artists.append(log.artist)
    artists = set(last_artists) | set(window_artists)
    titles = {log.title for log in logs[:TITLE_LAST_N] if log.title}
    return artists, titles
