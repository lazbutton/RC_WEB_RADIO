"""Connecteur Google Agenda : OAuth par compte, lecture incrémentale `syncToken`, écriture idempotente.

Le faux jumeau `FakeGoogle` reproduit la sémantique (syncToken, etag, 410 GONE) sans réseau.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from regie.kernel.connectors import Connector

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/userinfo.email"


class SyncTokenExpired(Exception):
    """Google a répondu 410 : il faut refaire une lecture complète."""


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def to_google(event: dict[str, Any]) -> dict[str, Any]:
    """Ligne Régie → corps d'événement Google."""
    body: dict[str, Any] = {"summary": event.get("title") or "(sans titre)", "description": event.get("description") or "", "location": event.get("location") or ""}
    if event.get("all_day"):
        body["start"] = {"date": str(event["starts_at"])[:10]}
        body["end"] = {"date": str(event["ends_at"])[:10]}
    else:
        body["start"] = {"dateTime": _iso(event["starts_at"])}
        body["end"] = {"dateTime": _iso(event["ends_at"])}
    body["status"] = "cancelled" if event.get("status") == "cancelled" else "confirmed"
    return body


def from_google(item: dict[str, Any]) -> dict[str, Any]:
    """Événement Google → ligne Régie (None si annulé)."""
    start = item.get("start") or {}
    end = item.get("end") or {}
    all_day = "date" in start
    starts = start.get("dateTime") or (start.get("date") + "T00:00:00+00:00" if start.get("date") else None)
    ends = end.get("dateTime") or (end.get("date") + "T00:00:00+00:00" if end.get("date") else None)
    return {
        "external_id": str(item.get("id") or ""),
        "title": item.get("summary") or "(sans titre)",
        "description": item.get("description") or "",
        "location": item.get("location") or "",
        "starts_at": starts,
        "ends_at": ends or starts,
        "all_day": all_day,
        "status": item.get("status") or "confirmed",
        "attendees": [{"email": a.get("email"), "status": a.get("responseStatus")} for a in item.get("attendees") or []],
        "html_link": item.get("htmlLink") or "",
        "etag": item.get("etag") or "",
        "remote_updated_at": item.get("updated"),
    }


class GoogleCalendarConnector(Connector):
    system = "google"
    label = "Google Agenda"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, timeout: float = 20.0) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.timeout = timeout
        self._access: dict[str, tuple[str, float]] = {}

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # --- OAuth ---------------------------------------------------------------------------

    def authorize_url(self, state: str) -> str:
        params = {"client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code", "scope": SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state}
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        res = httpx.post(TOKEN_URL, data={"code": code, "client_id": self.client_id, "client_secret": self.client_secret, "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"}, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        email = ""
        try:
            info = httpx.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=self.timeout)
            email = info.json().get("email", "") if info.status_code == 200 else ""
        except Exception:
            email = ""
        return {"refresh_token": data.get("refresh_token", ""), "access_token": data.get("access_token", ""), "expires_in": data.get("expires_in", 3600), "email": email}

    def access_token(self, refresh_token: str) -> str:
        cached = self._access.get(refresh_token)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        res = httpx.post(TOKEN_URL, data={"refresh_token": refresh_token, "client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "refresh_token"}, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        token = str(data["access_token"])
        self._access[refresh_token] = (token, time.time() + int(data.get("expires_in", 3600)))
        return token

    def _headers(self, refresh_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token(refresh_token)}", "Accept": "application/json"}

    # --- lecture / écriture ------------------------------------------------------------------

    def list_calendars(self, refresh_token: str) -> list[dict[str, Any]]:
        res = httpx.get(f"{API}/users/me/calendarList", headers=self._headers(refresh_token), timeout=self.timeout)
        res.raise_for_status()
        return [{"id": c.get("id"), "summary": c.get("summary"), "primary": bool(c.get("primary")), "color": c.get("backgroundColor", "#111"), "access": c.get("accessRole")} for c in res.json().get("items", [])]

    def pull_events(self, refresh_token: str, calendar_id: str, sync_token: str = "", horizon_days: int = 120) -> tuple[list[dict[str, Any]], str]:
        """Renvoie (événements bruts Google, nouveau syncToken). Lève SyncTokenExpired sur 410."""
        items: list[dict[str, Any]] = []
        params: dict[str, Any] = {"maxResults": 250, "showDeleted": "true", "singleEvents": "true"}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            now = datetime.now(timezone.utc)
            params["timeMin"] = (now - timedelta(days=30)).isoformat()
            params["timeMax"] = (now + timedelta(days=horizon_days)).isoformat()
        next_sync = ""
        page_token = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            res = httpx.get(f"{API}/calendars/{calendar_id}/events", params=params, headers=self._headers(refresh_token), timeout=self.timeout)
            if res.status_code == 410:
                raise SyncTokenExpired()
            res.raise_for_status()
            data = res.json()
            items.extend(data.get("items", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                next_sync = data.get("nextSyncToken", "")
                break
        return items, next_sync

    def push_event(self, refresh_token: str, calendar_id: str, event: dict[str, Any], external_id: str = "", etag: str = "") -> dict[str, Any]:
        body = to_google(event)
        headers = self._headers(refresh_token)
        if external_id:
            if etag:
                headers["If-Match"] = etag
            res = httpx.patch(f"{API}/calendars/{calendar_id}/events/{external_id}", json=body, headers=headers, timeout=self.timeout)
        else:
            res = httpx.post(f"{API}/calendars/{calendar_id}/events", json=body, headers=headers, timeout=self.timeout)
        res.raise_for_status()
        return res.json()

    def delete_event(self, refresh_token: str, calendar_id: str, external_id: str) -> None:
        res = httpx.delete(f"{API}/calendars/{calendar_id}/events/{external_id}", headers=self._headers(refresh_token), timeout=self.timeout)
        if res.status_code not in (204, 404, 410):
            res.raise_for_status()

    def health(self) -> dict[str, Any]:
        return {"ok": self.configured(), "redirect_uri": self.redirect_uri}


class FakeGoogle(Connector):
    """Agenda Google en mémoire : mêmes méthodes, sémantique syncToken/etag reproduite."""

    system = "google"
    label = "Google Agenda (faux)"

    def __init__(self) -> None:
        self.calendars: dict[str, dict[str, dict[str, Any]]] = {"primary": {}}
        self.log: list[tuple[str, str, str]] = []
        self._seq = 0
        self.expire_next_token = False
        self.fail_push = False

    def configured(self) -> bool:
        return True

    def authorize_url(self, state: str) -> str:
        return f"https://fake.google/auth?state={state}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        return {"refresh_token": f"refresh-{code}", "access_token": "at", "expires_in": 3600, "email": f"{code}@gmail.com"}

    def list_calendars(self, refresh_token: str) -> list[dict[str, Any]]:
        return [{"id": cid, "summary": cid, "primary": cid == "primary", "color": "#4285f4", "access": "owner"} for cid in self.calendars]

    def _stamp(self) -> str:
        self._seq += 1
        return f"{self._seq:06d}"

    def remote_add(self, calendar_id: str, title: str, start: str, end: str, external_id: str | None = None, **extra: Any) -> dict[str, Any]:
        """Simule une modification faite dans Google (par Lou, par exemple)."""
        cal = self.calendars.setdefault(calendar_id, {})
        ident = external_id or f"g{secrets.token_hex(4)}"
        seq = self._stamp()
        item = {"id": ident, "summary": title, "start": {"dateTime": start}, "end": {"dateTime": end}, "status": "confirmed", "etag": f'"{seq}"', "updated": f"2026-09-21T10:{int(seq) % 60:02d}:00Z", "_seq": int(seq), **extra}
        cal[ident] = item
        return item

    def remote_delete(self, calendar_id: str, external_id: str) -> None:
        cal = self.calendars.setdefault(calendar_id, {})
        if external_id in cal:
            seq = self._stamp()
            cal[external_id] = {**cal[external_id], "status": "cancelled", "etag": f'"{seq}"', "_seq": int(seq)}

    def pull_events(self, refresh_token: str, calendar_id: str, sync_token: str = "", horizon_days: int = 120) -> tuple[list[dict[str, Any]], str]:
        self.log.append(("pull", calendar_id, sync_token))
        if self.expire_next_token and sync_token:
            self.expire_next_token = False
            raise SyncTokenExpired()
        cal = self.calendars.setdefault(calendar_id, {})
        since = int(sync_token.split(":")[1]) if sync_token.startswith("tok:") else 0
        items = [dict(item) for item in cal.values() if int(item.get("_seq", 0)) > since]
        for item in items:
            item.pop("_seq", None)
        return items, f"tok:{self._seq}"

    def push_event(self, refresh_token: str, calendar_id: str, event: dict[str, Any], external_id: str = "", etag: str = "") -> dict[str, Any]:
        if self.fail_push:
            raise RuntimeError("Google indisponible")
        self.log.append(("push", calendar_id, external_id))
        cal = self.calendars.setdefault(calendar_id, {})
        body = to_google(event)
        if external_id and external_id in cal and etag and cal[external_id].get("etag") != etag:
            raise RuntimeError("412 etag mismatch")
        ident = external_id or f"g{secrets.token_hex(4)}"
        seq = self._stamp()
        item = {**cal.get(ident, {}), **body, "id": ident, "etag": f'"{seq}"', "updated": f"2026-09-21T11:{int(seq) % 60:02d}:00Z", "_seq": int(seq)}
        cal[ident] = item
        public = dict(item)
        public.pop("_seq", None)
        return public

    def delete_event(self, refresh_token: str, calendar_id: str, external_id: str) -> None:
        self.log.append(("delete", calendar_id, external_id))
        self.remote_delete(calendar_id, external_id)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "fake": True}
