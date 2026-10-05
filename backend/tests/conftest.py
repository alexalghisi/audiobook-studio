"""Shared fixtures. Integration tests use a real PostgreSQL and Redis.

Set STUDIO_TEST_DATABASE_URL / STUDIO_TEST_REDIS_URL to point elsewhere.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from studio.config import Settings
from studio.db.models import Base
from studio.db.session import make_engine, make_session_factory
from studio.runtime import Runtime
from studio.tts.base import SynthesisRequest, SynthesisResult, TTSProvider, Voice
from studio.tts.offline import FormantProvider

DATABASE_URL = os.environ.get(
    "STUDIO_TEST_DATABASE_URL", "postgresql+asyncpg://synckit:synckit@localhost:5432/audiobook_studio_test"
)
REDIS_URL = os.environ.get("STUDIO_TEST_REDIS_URL", "redis://localhost:6379/13")


class CountingProvider:
    """Wraps a provider and records every synthesis request that reaches it."""

    def __init__(self, inner: TTSProvider | None = None) -> None:
        self.inner: TTSProvider = inner or FormantProvider()
        self.texts: list[str] = []
        self.fail_texts: dict[str, Exception] = {}
        self.delay = 0.0

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def default_voice_id(self) -> str:
        return self.inner.default_voice_id

    @property
    def default_model_id(self) -> str:
        return self.inner.default_model_id

    @property
    def max_concurrency(self) -> int:
        return self.inner.max_concurrency

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        import asyncio

        self.texts.append(request.text)
        if self.delay:
            await asyncio.sleep(self.delay)
        for needle, error in self.fail_texts.items():
            if needle in request.text:
                raise error
        return await self.inner.synthesize(request)

    async def list_voices(self) -> list[Voice]:
        return await self.inner.list_voices()

    async def aclose(self) -> None:
        await self.inner.aclose()


async def _ensure_database(url: str) -> None:
    base, name = url.rsplit("/", 1)
    admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    await _ensure_database(DATABASE_URL)
    eng = make_engine(DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def clean_db(engine: AsyncEngine) -> AsyncEngine:
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    return engine


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        redis_url=REDIS_URL,
        data_dir=tmp_path / "data",
        queue_prefix=f"test-{uuid.uuid4().hex[:8]}",
        elevenlabs_api_key=None,
        worker_lease_seconds=1.5,
        worker_poll_seconds=0.1,
        segment_budget=120,
    )


@pytest.fixture
def provider() -> CountingProvider:
    return CountingProvider()


@pytest.fixture
async def rt(clean_db: AsyncEngine, settings: Settings, provider: CountingProvider) -> AsyncIterator[Runtime]:
    rt = Runtime(
        settings=settings,
        engine=clean_db,
        sessions=make_session_factory(clean_db),
        redis=Redis.from_url(settings.redis_url),
        provider=provider,
    )
    yield rt
    async for key in rt.redis.scan_iter(match=f"{settings.queue_prefix}:*"):
        await rt.redis.delete(key)
    await rt.redis.aclose()
