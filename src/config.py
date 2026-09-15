"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Annotated, List, Optional

from pydantic import field_validator, Field
from pydantic_settings import BaseSettings, NoDecode


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # "extra": "ignore" lets this Settings class share one .env file with the
    # legacy bot.py (BOT_TOKEN, OWNER_ID, PORT, ...) instead of refusing to start.
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # Application
    app_env: str = "development"
    app_debug: bool = False
    app_secret_key: str = "change-me"

    # Telegram
    telegram_bot_token: str
    telegram_webhook_url: Optional[str] = None
    # NoDecode: without it pydantic-settings JSON-decodes this list from the
    # environment and rejects plain values like "123456789" or "1,2,3"
    # before the validator below ever runs.
    telegram_authorized_user_ids: Annotated[List[int], NoDecode] = []

    @field_validator("telegram_authorized_user_ids", mode="before")
    @classmethod
    def parse_authorized_users(cls, v: str | int | list[int]) -> List[int]:
        """Accept a single id, a comma/space separated string, or a JSON list.

        pydantic-settings may hand us a bare int for a single id
        (e.g. TELEGRAM_AUTHORIZED_USER_IDS=123456789), which would otherwise
        fail list validation.
        """
        if v is None or v == "":
            return []
        if isinstance(v, bool):
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("[") and s.endswith("]"):
                s = s[1:-1]
            parts = s.replace(" ", ",").split(",")
            return [int(p.strip().strip("'\"")) for p in parts if p.strip().strip("'\"")]
        return v

    # YouTube OAuth 2.0
    youtube_client_id: str
    youtube_client_secret: str
    youtube_redirect_uri: str = "http://localhost:8000/oauth/youtube/callback"
    youtube_scopes: str = (
        "https://www.googleapis.com/auth/youtube.upload,"
        "https://www.googleapis.com/auth/youtube,"
        "https://www.googleapis.com/auth/youtube.force-ssl"
    )

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/youtube_bot"
    database_url_sync: str = "[REDACTED-SECRET]"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Object Storage
    storage_backend: str = "local"
    storage_bucket: str = "youtube-bot-media"
    storage_endpoint_url: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # Encryption
    token_encryption_key: str

    # Upload Limits
    max_file_size_mb: int = 2048
    supported_video_formats: str = ".mp4,.mov,.avi,.mkv,.webm"

    # TikTok
    tiktok_api_key: Optional[str] = None
    tiktok_api_secret: Optional[str] = None

    # Hashtag Suggestions
    hashtag_suggestion_api_key: Optional[str] = None
    hashtag_suggestion_api_url: Optional[str] = None

    # Logging
    log_level: str = "INFO"

    @property
    def youtube_scopes_list(self) -> List[str]:
        return [s.strip() for s in self.youtube_scopes.split(",")]

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def supported_video_formats_list(self) -> List[str]:
        return [f.strip() for f in self.supported_video_formats.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
