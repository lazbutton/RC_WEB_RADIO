"""Connecteur Outlive : lecture seule des événements d'Orléans (API v1 partenaire, PostgREST anonyme en secours).

Le mappage est défensif : un champ inconnu est ignoré et signalé dans `warnings`, jamais bloquant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from regie.kernel.connectors import Change, Connector, PullResult

KNOWN_FIELDS = {
    "id", "title", "description", "date", "end_date", "end_time", "category", "status", "address", "image_url", "external_url",
    "external_url_label", "price", "price_min", "price_max", "is_pay_what_you_want", "presale_price", "location_id", "room_id",
    "latitude", "longitude", "updated_at", "created_at", "archived", "is_full", "agenda_types", "show_on_app", "is_featured",
    "hide_from_home", "city_id", "tag_ids", "capacity", "door_opening_time", "instagram_url", "facebook_url", "scraping_url",
    "created_by", "subscriber_price", "locations", "event_organizers", "event_artists", "cities", "organizer", "venue", "artists",
    "organizer_id", "organizer_name", "venue_name", "url", "starts_at", "ends_at", "price_label",
}
SELECT = "*,locations(id,name,address,latitude,longitude,capacity,website_url),cities(label),event_organizers(organizers(id,name,website_url),locations(id,name)),event_artists(artists(id,name))"


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _price(raw: dict[str, Any]) -> str:
    if raw.get("is_pay_what_you_want"):
        return "prix libre"
    if raw.get("price_label"):
        return str(raw["price_label"])
    low, high, single = raw.get("price_min"), raw.get("price_max"), raw.get("price")
    if low is not None and high is not None and low != high:
        return f"{_num(low)}–{_num(high)} €"
    value = _first(single, low, high)
    if value is None:
        return ""
    return "gratuit" if float(value) == 0 else f"{_num(value)} €"


def _num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:.2f}".rstrip("0").rstrip(".")


def _dt(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def map_event(raw: dict[str, Any], radio_campus_organizer_id: str = "") -> tuple[dict[str, Any], list[str]]:
    """Ligne Outlive (PostgREST imbriqué ou API plate) → ligne `outlive_events`."""
    warnings: list[str] = []
    unknown = sorted(set(raw) - KNOWN_FIELDS)
    if unknown:
        warnings.append("champs ignorés : " + ", ".join(unknown[:12]))
    event_id = str(raw.get("id") or "").strip()
    if not event_id:
        raise ValueError("événement sans id")
    starts = _dt(_first(raw.get("date"), raw.get("starts_at")))
    if not starts:
        raise ValueError(f"événement {event_id} sans date")
    ends = _dt(_first(raw.get("end_date"), raw.get("ends_at")))
    if not ends and raw.get("end_time"):
        try:
            hh, mm = str(raw["end_time"]).split(":")[:2]
            base = datetime.fromisoformat(starts)
            candidate = base.replace(hour=int(hh), minute=int(mm))
            if candidate <= base:
                candidate += timedelta(days=1)
            ends = candidate.isoformat()
        except (ValueError, AttributeError):
            warnings.append("end_time illisible")
    venue = raw.get("locations") if isinstance(raw.get("locations"), dict) else (raw.get("venue") if isinstance(raw.get("venue"), dict) else {})
    venue = venue or {}
    organizer: dict[str, Any] = {}
    for link in raw.get("event_organizers") or []:
        if isinstance(link, dict):
            if isinstance(link.get("organizers"), dict):
                organizer = link["organizers"]
                break
            if isinstance(link.get("locations"), dict) and not organizer:
                organizer = {**link["locations"], "_is_location": True}
    if not organizer and isinstance(raw.get("organizer"), dict):
        organizer = raw["organizer"]
    if not organizer and raw.get("organizer_id"):
        organizer = {"id": raw.get("organizer_id"), "name": raw.get("organizer_name") or ""}
    artists = []
    for link in raw.get("event_artists") or raw.get("artists") or []:
        artist = link.get("artists") if isinstance(link, dict) and isinstance(link.get("artists"), dict) else link
        if isinstance(artist, dict) and artist.get("name"):
            artists.append({"id": str(artist.get("id") or ""), "name": str(artist["name"])})
    city = ""
    if isinstance(raw.get("cities"), dict):
        city = str(raw["cities"].get("label") or raw["cities"].get("name") or "")
    organizer_id = str(organizer.get("id") or "")
    row = {
        "id": event_id,
        "title": str(raw.get("title") or "").strip() or "(sans titre)",
        "description": str(raw.get("description") or ""),
        "starts_at": starts,
        "ends_at": ends,
        "category": str(raw.get("category") or ""),
        "status": str(raw.get("status") or "approved"),
        "city": city,
        "address": str(_first(raw.get("address"), venue.get("address")) or ""),
        "venue_id": str(_first(venue.get("id"), raw.get("location_id")) or ""),
        "venue_name": str(_first(venue.get("name"), raw.get("venue_name")) or ""),
        "organizer_id": organizer_id,
        "organizer_name": str(organizer.get("name") or ""),
        "artists": artists,
        "price": _price(raw),
        "url": str(_first(raw.get("external_url"), raw.get("url")) or ""),
        "image_url": str(raw.get("image_url") or ""),
        "agenda_types": [str(t) for t in (raw.get("agenda_types") or [])],
        "is_radio_campus": bool(radio_campus_organizer_id and organizer_id == radio_campus_organizer_id),
        "source_updated_at": _dt(raw.get("updated_at")),
        "archived": bool(raw.get("archived")),
        "raw": raw,
        "warnings": warnings,
    }
    if not row["venue_name"] and not row["address"]:
        warnings.append("lieu inconnu")
    return row, warnings


class OutliveConnector(Connector):
    system = "outlive"
    label = "Outlive (agenda d'Orléans)"
    read_only = True

    def __init__(self, api_url: str, partner_key: str, supabase_url: str, anon_key: str, radio_campus_organizer_id: str = "", timeout: float = 20.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.partner_key = partner_key
        self.supabase_url = supabase_url.rstrip("/")
        self.anon_key = anon_key
        self.radio_campus_organizer_id = radio_campus_organizer_id
        self.timeout = timeout

    def configured(self) -> bool:
        return bool(self.partner_key or self.anon_key)

    def fetch_raw(self, since: datetime | None = None, horizon_days: int = 120) -> list[dict[str, Any]]:
        since = since or datetime.now(timezone.utc) - timedelta(days=7)
        if self.partner_key:
            try:
                return self._fetch_api(since, horizon_days)
            except Exception:
                if not self.anon_key:
                    raise
        return self._fetch_postgrest(since, horizon_days)

    def _fetch_api(self, since: datetime, horizon_days: int) -> list[dict[str, Any]]:
        params = {"from": since.date().isoformat(), "to": (since + timedelta(days=horizon_days)).date().isoformat(), "limit": 1000}
        res = httpx.get(f"{self.api_url}/api/v1/events", params=params, headers={"X-Partner-Key": self.partner_key, "Accept": "application/json"}, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        items = data.get("events") if isinstance(data, dict) else data
        return [row for row in (items or []) if isinstance(row, dict)]

    def _fetch_postgrest(self, since: datetime, horizon_days: int) -> list[dict[str, Any]]:
        params = {
            "select": SELECT,
            "status": "eq.approved",
            "archived": "eq.false",
            "date": f"gte.{since.isoformat()}",
            "order": "date.asc",
            "limit": "2000",
        }
        headers = {"apikey": self.anon_key, "Authorization": f"Bearer {self.anon_key}", "Accept": "application/json"}
        res = httpx.get(f"{self.supabase_url}/rest/v1/events", params=params, headers=headers, timeout=self.timeout)
        res.raise_for_status()
        return [row for row in res.json() if isinstance(row, dict)]

    def pull(self, cursor: dict[str, Any]) -> PullResult:
        since_raw = cursor.get("since")
        since = datetime.fromisoformat(since_raw) if since_raw else None
        rows = self.fetch_raw(since)
        changes: list[Change] = []
        for raw in rows:
            try:
                mapped, _warnings = map_event(raw, self.radio_campus_organizer_id)
            except ValueError:
                continue
            changes.append(Change(external_id=mapped["id"], kind="deleted" if mapped["archived"] or mapped["status"] != "approved" else "updated", data=mapped, etag=mapped.get("source_updated_at") or ""))
        return PullResult(changes=changes, cursor={"since": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(), "last_pull": datetime.now(timezone.utc).isoformat(), "count": len(changes)})

    def health(self) -> dict[str, Any]:
        try:
            rows = self.fetch_raw(datetime.now(timezone.utc), horizon_days=7)
            return {"ok": True, "sample": len(rows)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}


class FakeOutlive(Connector):
    """Faux jumeau : renvoie un échantillon figé, sans réseau."""

    system = "outlive"
    label = "Outlive (faux)"
    read_only = True

    def __init__(self, rows: list[dict[str, Any]], radio_campus_organizer_id: str = "") -> None:
        self.rows = rows
        self.radio_campus_organizer_id = radio_campus_organizer_id
        self.pulls = 0

    def fetch_raw(self, since=None, horizon_days: int = 120) -> list[dict[str, Any]]:
        return list(self.rows)

    def pull(self, cursor: dict[str, Any]) -> PullResult:
        self.pulls += 1
        return OutliveConnector.pull(self, cursor)  # type: ignore[arg-type]
