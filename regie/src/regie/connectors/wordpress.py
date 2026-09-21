"""Connecteur WordPress (REST, mot de passe d'application) : brouillon puis publication d'un article de podcast."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from regie.kernel.connectors import Connector


class WordPressConnector(Connector):
    system = "wordpress"
    label = "Site WordPress"

    def __init__(self, base_url: str, user: str, app_password: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.app_password = app_password
        self.timeout = timeout

    def configured(self) -> bool:
        return bool(self.base_url and self.user and self.app_password)

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self.user}:{self.app_password}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def upsert_post(self, external_id: str, title: str, content: str, status: str = "draft", excerpt: str = "", categories: list[int] | None = None, slug: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"title": title, "content": content, "status": status, "excerpt": excerpt}
        if categories:
            body["categories"] = categories
        if slug:
            body["slug"] = slug
        url = f"{self.base_url}/wp-json/wp/v2/posts" + (f"/{external_id}" if external_id else "")
        res = httpx.post(url, json=body, headers=self._headers(), timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        return {"id": str(data.get("id")), "url": data.get("link") or "", "status": data.get("status")}

    def upload_media(self, filename: str, data: bytes, mime: str) -> dict[str, Any]:
        res = httpx.post(f"{self.base_url}/wp-json/wp/v2/media", content=data, headers={**self._headers(), "Content-Type": mime, "Content-Disposition": f'attachment; filename="{filename}"'}, timeout=max(self.timeout, 300))
        res.raise_for_status()
        payload = res.json()
        return {"id": str(payload.get("id")), "url": payload.get("source_url") or ""}

    def health(self) -> dict[str, Any]:
        try:
            res = httpx.get(f"{self.base_url}/wp-json/wp/v2/users/me", headers=self._headers(), timeout=self.timeout)
            return {"ok": res.status_code == 200, "status": res.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}


class FakeWordPress(Connector):
    system = "wordpress"
    label = "WordPress (faux)"

    def __init__(self) -> None:
        self.posts: dict[str, dict[str, Any]] = {}
        self.media: list[dict[str, Any]] = []
        self.fail = False
        self._seq = 100

    def configured(self) -> bool:
        return True

    def upsert_post(self, external_id: str, title: str, content: str, status: str = "draft", excerpt: str = "", categories=None, slug: str = "") -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("WordPress indisponible")
        ident = external_id or str(self._seq)
        if not external_id:
            self._seq += 1
        self.posts[ident] = {"id": ident, "title": title, "content": content, "status": status, "excerpt": excerpt, "slug": slug}
        return {"id": ident, "url": f"https://orleans.radiocampus.org/?p={ident}", "status": status}

    def upload_media(self, filename: str, data: bytes, mime: str) -> dict[str, Any]:
        self.media.append({"filename": filename, "size": len(data), "mime": mime})
        return {"id": str(len(self.media)), "url": f"https://orleans.radiocampus.org/wp-content/uploads/{filename}"}

    def health(self) -> dict[str, Any]:
        return {"ok": True, "fake": True}
