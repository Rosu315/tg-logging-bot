"""Runtime configuration, read from environment variables or a local .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = Field(..., alias="BOT_TOKEN")
    api_base_url: str | None = Field(
        None,
        alias="API_BASE_URL",
        description="Reverse-proxy base URL in front of api.telegram.org "
                    "(e.g. a Cloudflare Worker), used when api.telegram.org "
                    "is unreachable directly. Leave unset to talk to Telegram "
                    "directly.",
    )

    db_path: Path = Field(Path("data/vestige.db"), alias="DB_PATH")
    media_dir: Path = Field(Path("data/media"), alias="MEDIA_DIR")

    cache_media: bool = Field(False, alias="CACHE_MEDIA")
    max_cache_file_mb: int = Field(20, alias="MAX_CACHE_FILE_MB")

    retention_days: int = Field(14, alias="RETENTION_DAYS")
    cleanup_interval_minutes: int = Field(60, alias="CLEANUP_INTERVAL_MINUTES")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    allowed_usernames: str = Field(
        "Rosu31",
        alias="ALLOWED_USERNAMES",
        description="Startup seed for the connection allowlist (comma/space "
                    "separated, without @). Only used to populate an empty "
                    "allowlist on first run; after that it lives in the "
                    "database and is managed via /users, /allow, /deny.",
    )
    admin_username: str = Field(
        "Rosu31",
        alias="ADMIN_USERNAME",
        description="The only Telegram @username allowed to manage the "
                    "connection allowlist (/users, /allow, /deny).",
    )

    @property
    def max_cache_file_bytes(self) -> int:
        return self.max_cache_file_mb * 1024 * 1024

    @property
    def allowed_usernames_set(self) -> frozenset[str]:
        raw = self.allowed_usernames.replace(",", " ").split()
        return frozenset(u.lstrip("@").lower() for u in raw if u.lstrip("@"))

    def is_admin(self, username: str | None) -> bool:
        return bool(username) and username.lower() == self.admin_username.lower()

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cache_media:
            self.media_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_dirs()
    return settings
