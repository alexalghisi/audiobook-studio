from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDIO_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://synckit:synckit@localhost:5432/audiobook_studio"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    queue_prefix: str = "studio"

    elevenlabs_api_key: str | None = Field(
        None, validation_alias=AliasChoices("ELEVENLABS_API_KEY", "STUDIO_ELEVENLABS_API_KEY")
    )
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_concurrency: int = 4

    segment_budget: int = 800
    segment_hard_limit: int = 4500

    job_max_attempts: int = 3
    worker_lease_seconds: float = 15.0
    worker_poll_seconds: float = 1.0

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"


@lru_cache
def get_settings() -> Settings:
    return Settings()
