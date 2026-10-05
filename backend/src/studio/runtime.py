"""Wires settings into the long-lived objects shared by the API and the worker."""

from __future__ import annotations

from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from studio.audio.store import ContentStore
from studio.config import Settings, get_settings
from studio.db.session import SessionFactory, make_engine, make_session_factory
from studio.jobs.events import EventBus
from studio.jobs.handlers import default_handlers
from studio.jobs.queue import JobQueue
from studio.jobs.worker import Worker
from studio.services.audio_cache import AudioCache
from studio.services.jobs import JobService
from studio.services.projects import ProjectService
from studio.services.synthesis import Synthesizer
from studio.tts.base import TTSProvider
from studio.tts.factory import build_provider


@dataclass
class Runtime:
    settings: Settings
    engine: AsyncEngine
    sessions: SessionFactory
    redis: Redis
    provider: TTSProvider
    queue: JobQueue = field(init=False)
    events: EventBus = field(init=False)
    cache: AudioCache = field(init=False)
    projects: ProjectService = field(init=False)
    jobs: JobService = field(init=False)
    synthesizer: Synthesizer = field(init=False)

    def __post_init__(self) -> None:
        s = self.settings
        self.queue = JobQueue(self.redis, s.queue_prefix, s.worker_lease_seconds)
        self.events = EventBus(self.redis, s.queue_prefix)
        self.cache = AudioCache(ContentStore(s.audio_dir), self.sessions)
        self.projects = ProjectService(
            self.sessions,
            self.cache,
            self.provider.name,
            self.provider.default_voice_id,
            self.provider.default_model_id,
            s.segment_budget,
            s.segment_hard_limit,
        )
        self.jobs = JobService(self.sessions, self.queue, self.events, s.job_max_attempts)
        self.synthesizer = Synthesizer(self.sessions, self.cache, self.provider)

    @classmethod
    def from_settings(cls, settings: Settings | None = None, provider: TTSProvider | None = None) -> Runtime:
        settings = settings or get_settings()
        engine = make_engine(settings.database_url)
        return cls(
            settings=settings,
            engine=engine,
            sessions=make_session_factory(engine),
            redis=Redis.from_url(settings.redis_url),
            provider=provider or build_provider(settings),
        )

    def worker(
        self, worker_id: str | None = None, concurrency: int = 2, retry_base_seconds: float = 2.0
    ) -> Worker:
        return Worker(
            self.sessions,
            self.queue,
            self.events,
            default_handlers(self.synthesizer),
            worker_id=worker_id,
            concurrency=concurrency,
            poll_seconds=self.settings.worker_poll_seconds,
            retry_base_seconds=retry_base_seconds,
        )

    async def aclose(self) -> None:
        await self.provider.aclose()
        await self.redis.aclose()
        await self.engine.dispose()
