from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    inboxzero_data: str = "/data"
    inboxzero_token: str = ""
    imap_host: str = "mail.radio-campus.org"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_fast_model: str = "claude-sonnet-4-5"
    anthropic_effort: str = "high"
    anthropic_workspace_id: str = ""
    inboxzero_poll_seconds: int = 180
    inboxzero_body_chars: int = 4000
    inboxzero_bind: str = "0.0.0.0"
    inboxzero_port: int = 30128
    notion_token: str = ""
    notion_database_id: str = "71b5299cc0494581954031510d690601"
    notion_data_source_id: str = "d01bbc98-e036-419d-9e14-29045e7db211"
    notion_project_id: str = "3d63b6b3c9bf8059967cf38aea00c84b"

    @property
    def data_dir(self) -> str:
        return self.inboxzero_data

    @property
    def db_path(self) -> str:
        return os.path.join(self.inboxzero_data, "inboxzero.db")

    @property
    def attachments_dir(self) -> str:
        return os.path.join(self.inboxzero_data, "attachments")

    @property
    def imap_ready(self) -> bool:
        return bool(self.imap_user and self.imap_password)

    @property
    def llm_ready(self) -> bool:
        return bool(self.anthropic_api_key)

    def notion_secret(self, stored: str = "") -> str:
        return (stored or self.notion_token or "").strip()

    def notion_ready(self, stored: str = "") -> bool:
        return bool(self.notion_secret(stored))


@lru_cache
def get_settings() -> Settings:
    return Settings()
