from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Tout vient de l'environnement (secrets.env sur Nasgul, jamais du dépôt)."""

    model_config = SettingsConfigDict(extra="ignore")

    regie_dsn: str = "postgresql://regie:regie@127.0.0.1:5432/regie"
    regie_secret: str = ""
    regie_data: str = "/data"
    regie_media_root: str = "/media"
    regie_media_writable: str = "00-inbox,40-emissions"
    regie_bind: str = "0.0.0.0"
    regie_port: int = 30130
    regie_workers: int = 1
    regie_public_url: str = ""
    regie_org_slug: str = "radio-campus"
    regie_org_name: str = "Radio Campus Orléans"
    regie_bootstrap_admin_email: str = ""
    regie_bootstrap_admin_password: str = ""
    regie_bootstrap_admin_name: str = "Laz"
    regie_session_days: int = 30
    regie_log_json: bool = True
    regie_run_workers: bool = True
    regie_backup_max_age_hours: int = 48

    imap_host: str = "mail.radio-campus.org"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    mail_poll_seconds: int = 180
    mail_body_chars: int = 4000
    mail_poll_seconds: int = 180
    mail_body_chars: int = 4000
    mail_scan_days: int = 30

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_fast_model: str = "claude-sonnet-4-5"
    anthropic_effort: str = "high"
    anthropic_workspace_id: str = ""

    notion_token: str = ""
    notion_database_id: str = "71b5299cc0494581954031510d690601"
    notion_data_source_id: str = "d01bbc98-e036-419d-9e14-29045e7db211"
    notion_project_id: str = "3d63b6b3c9bf8059967cf38aea00c84b"

    outlive_api_url: str = "https://www.outlive.fr"
    outlive_partner_key: str = ""
    outlive_supabase_url: str = "https://tkyuiltgvqcgebnqdgmb.supabase.co"
    outlive_anon_key: str = ""
    outlive_radio_campus_organizer_id: str = "9d6d803d-4a7e-4118-beba-1744a1159b99"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    wordpress_url: str = "https://orleans.radiocampus.org"
    wordpress_user: str = ""
    wordpress_app_password: str = ""

    edge_provider: str = ""  # vercel | cloudflare | none
    edge_token: str = ""
    edge_project: str = ""
    edge_account_id: str = ""

    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:regie@orleans.radiocampus.org"

    @property
    def data_dir(self) -> str:
        return self.regie_data

    @property
    def attachments_dir(self) -> str:
        return os.path.join(self.regie_data, "attachments")

    @property
    def media_writable(self) -> list[str]:
        return [part.strip().strip("/") for part in self.regie_media_writable.split(",") if part.strip()]

    @property
    def imap_ready(self) -> bool:
        return bool(self.imap_user and self.imap_password)

    @property
    def llm_ready(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def google_ready(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def wordpress_ready(self) -> bool:
        return bool(self.wordpress_user and self.wordpress_app_password)

    def notion_secret(self, stored: str = "") -> str:
        return (stored or self.notion_token or "").strip()

    def notion_ready(self, stored: str = "") -> bool:
        return bool(self.notion_secret(stored))


@lru_cache
def get_settings() -> Settings:
    return Settings()
