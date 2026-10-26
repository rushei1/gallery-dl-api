"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed settings for the gallery-api service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(alias="API_KEY")
    download_dir: str = Field(default="downloads", alias="DOWNLOAD_DIR")
    job_expiry_minutes: int = Field(default=5, alias="JOB_EXPIRY_MINUTES")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    server_port: int | None = Field(default=None, alias="SERVER_PORT")
    max_downloads: int = Field(default=5, alias="MAX_DOWNLOADS")
    download_timeout_seconds: int = Field(default=600, alias="DOWNLOAD_TIMEOUT_SECONDS")
    max_url_length: int = Field(default=2048, alias="MAX_URL_LENGTH")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    gallery_dl_cookie_file: str | None = Field(default=None, alias="GALLERY_DL_COOKIE_FILE")
    gallery_dl_youtube_cookie_file: str | None = Field(
        default=None, alias="GALLERY_DL_YOUTUBE_COOKIE_FILE"
    )
    gallery_dl_instagram_cookie_file: str | None = Field(
        default=None, alias="GALLERY_DL_INSTAGRAM_COOKIE_FILE"
    )
    gallery_dl_config_file: str | None = Field(
        default="gallery-dl.conf", alias="GALLERY_DL_CONFIG_FILE"
    )

    def resolve_config_path(self) -> Path | None:
        """Resolve gallery-dl config file path."""
        if not self.gallery_dl_config_file:
            return None
        config_path = Path(self.gallery_dl_config_file)
        if not config_path.is_absolute():
            config_path = PROJECT_ROOT / config_path
        config_path = config_path.resolve()
        return config_path if config_path.is_file() else None

    def resolve_cookie_path(self, path: str | None) -> Path | None:
        """Resolve a cookie file path relative to the project root."""
        if not path:
            return None
        cookie_path = Path(path)
        if not cookie_path.is_absolute():
            cookie_path = PROJECT_ROOT / cookie_path
        cookie_path = cookie_path.resolve()
        return cookie_path if cookie_path.is_file() else None

    def cookie_file_for_url(self, url: str) -> Path | None:
        """Pick the cookie file for a download URL based on its host."""
        from app.utils import cookie_file_key_for_url

        key = cookie_file_key_for_url(url)
        if key == "instagram":
            resolved = self.resolve_cookie_path(self.gallery_dl_instagram_cookie_file)
            if resolved:
                return resolved
        elif key == "youtube":
            resolved = self.resolve_cookie_path(self.gallery_dl_youtube_cookie_file)
            if resolved:
                return resolved
        return self.resolve_cookie_path(self.gallery_dl_cookie_file)

    @model_validator(mode="after")
    def resolve_listen_port(self) -> Settings:
        """Prefer Pterodactyl SERVER_PORT over PORT."""
        if self.server_port is not None:
            self.port = self.server_port
        elif os.environ.get("SERVER_PORT"):
            try:
                self.port = int(os.environ["SERVER_PORT"])
            except ValueError:
                pass
        return self

    @field_validator("api_key")
    @classmethod
    def api_key_must_be_set(cls, value: str) -> str:
        if not value or value.strip() == "" or value == "change-me-to-a-secure-random-key":
            raise ValueError(
                "API_KEY must be set to a secure value in environment or .env"
            )
        return value

    @property
    def download_path(self) -> Path:
        """Absolute path to the download directory."""
        path = Path(self.download_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
