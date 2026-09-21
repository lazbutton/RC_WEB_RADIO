"""Charge button/brand.json (identité, labels, URLs). Aucun secret."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

Brand = dict[str, Any]


def brand_path() -> Path:
    env = os.environ.get("BUTTON_BRAND", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path
    here = Path(__file__).resolve().parent / "brand.json"
    if here.is_file():
        return here
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "button" / "brand.json"
        if candidate.is_file():
            return candidate
        packaged = parent / "brand.json"
        if packaged.is_file() and packaged.parent.name in {"ingest", "radiotomate", "inboxzero", "button"}:
            return packaged
    raise FileNotFoundError("brand.json introuvable (BUTTON_BRAND ou dossier button/)")


def interpolate(template: str, data: Brand | None = None) -> str:
    brand = data or load_brand()
    suite = brand.get("suite") or {}
    infra = brand.get("infra") or {}
    return (
        (template or "")
        .replace("{name}", str(suite.get("name") or ""))
        .replace("{short}", str(suite.get("short") or ""))
        .replace("{share}", str(infra.get("smbShare") or ""))
    )


@lru_cache
def load_brand() -> Brand:
    path = brand_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("brand.json invalide")
    return payload


def label(group: str, key: str, data: Brand | None = None) -> str:
    brand = data or load_brand()
    raw = ((brand.get("labels") or {}).get(group) or {}).get(key) or ""
    return interpolate(str(raw), brand)


def app_url(name: str, field: str = "url", data: Brand | None = None) -> str:
    brand = data or load_brand()
    return str(((brand.get("apps") or {}).get(name) or {}).get(field) or "")


def infra(key: str, data: Brand | None = None) -> str:
    brand = data or load_brand()
    return str((brand.get("infra") or {}).get(key) or "")
