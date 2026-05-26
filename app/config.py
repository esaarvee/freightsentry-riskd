"""Settings — pydantic-settings, no env prefix.

Env var names match field names verbatim (uppercased). `Settings()` is
constructed once at app lifespan via `get_settings()` (lru_cache); tests
override by `monkeypatch.setenv(...)` then `get_settings.cache_clear()`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    hmac_secret: str

    api_token_prefix: str = "rsk_"
    maxmind_license_key: str = ""
    ip2proxy_download_token: str = ""
    enrichment_data_dir: Path = Path("/app/data/enrichment")
    log_level: str = "INFO"
    auth_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
