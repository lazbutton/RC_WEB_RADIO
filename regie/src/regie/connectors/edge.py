"""Bord public : dépôt de fichiers statiques (blocs iframe, JSON, RSS) vers Vercel, Cloudflare Pages ou un dossier local.

Nasgul n'est jamais exposé : Régie pousse, personne ne tire. Une régénération complète répare toute perte du bord.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any

import httpx

from regie.kernel.connectors import Connector


class EdgeConnector(Connector):
    system = "edge"
    label = "Bord public (blocs iframe)"

    def __init__(self, provider: str, token: str = "", project: str = "", account_id: str = "", local_dir: str = "", public_url: str = "", timeout: float = 60.0) -> None:
        self.provider = (provider or "").lower()
        self.token = token
        self.project = project
        self.account_id = account_id
        self.local_dir = local_dir
        self.public_url = public_url.rstrip("/")
        self.timeout = timeout

    def configured(self) -> bool:
        if self.provider == "dir":
            return bool(self.local_dir)
        if self.provider == "vercel":
            return bool(self.token and self.project)
        if self.provider == "cloudflare":
            return bool(self.token and self.project and self.account_id)
        return False

    def deploy(self, files: dict[str, bytes]) -> dict[str, Any]:
        """files : chemin relatif → contenu. Renvoie {url, count, provider}."""
        if self.provider == "dir":
            return self._deploy_dir(files)
        if self.provider == "vercel":
            return self._deploy_vercel(files)
        if self.provider == "cloudflare":
            return self._deploy_cloudflare(files)
        raise RuntimeError("bord public non configuré (EDGE_PROVIDER = vercel | cloudflare | dir)")

    def _deploy_dir(self, files: dict[str, bytes]) -> dict[str, Any]:
        root = Path(self.local_dir)
        staging = root.with_name(root.name + ".next")
        if staging.exists():
            shutil.rmtree(staging)
        for rel, data in files.items():
            target = staging / rel.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if root.exists():
            shutil.rmtree(root)
        staging.rename(root)
        return {"url": self.public_url or str(root), "count": len(files), "provider": "dir"}

    def _deploy_vercel(self, files: dict[str, bytes]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        payload_files = []
        for rel, data in files.items():
            digest = hashlib.sha1(data).hexdigest()
            httpx.post("https://api.vercel.com/v2/files", content=data, headers={**headers, "x-vercel-digest": digest, "Content-Type": "application/octet-stream"}, timeout=self.timeout).raise_for_status()
            payload_files.append({"file": rel.lstrip("/"), "sha": digest, "size": len(data)})
        res = httpx.post("https://api.vercel.com/v13/deployments", json={"name": self.project, "files": payload_files, "target": "production", "projectSettings": {"framework": None}}, headers=headers, timeout=self.timeout)
        res.raise_for_status()
        data = res.json()
        return {"url": "https://" + (data.get("alias") or [data.get("url")])[0] if data.get("alias") or data.get("url") else self.public_url, "count": len(files), "provider": "vercel", "id": data.get("id")}

    def _deploy_cloudflare(self, files: dict[str, bytes]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        manifest: dict[str, str] = {}
        uploads = []
        for rel, data in files.items():
            digest = hashlib.blake2b(data, digest_size=16).hexdigest()
            manifest["/" + rel.lstrip("/")] = digest
            import base64

            uploads.append({"key": digest, "value": base64.b64encode(data).decode(), "metadata": {"contentType": mimetypes.guess_type(rel)[0] or "application/octet-stream"}, "base64": True})
        jwt = httpx.get(f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/pages/projects/{self.project}/upload-token", headers=headers, timeout=self.timeout)
        jwt.raise_for_status()
        upload_token = jwt.json()["result"]["jwt"]
        httpx.post("https://api.cloudflare.com/client/v4/pages/assets/upload", json=uploads, headers={"Authorization": f"Bearer {upload_token}"}, timeout=max(self.timeout, 300)).raise_for_status()
        res = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/pages/projects/{self.project}/deployments", files={"manifest": (None, json.dumps(manifest))}, headers=headers, timeout=self.timeout)
        res.raise_for_status()
        result = res.json().get("result", {})
        return {"url": result.get("url") or self.public_url, "count": len(files), "provider": "cloudflare", "id": result.get("id")}

    def health(self) -> dict[str, Any]:
        return {"ok": self.configured(), "provider": self.provider, "public_url": self.public_url}
