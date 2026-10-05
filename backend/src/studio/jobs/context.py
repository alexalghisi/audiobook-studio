from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import update

from studio.db.models import Job, JobStatus
from studio.db.session import SessionFactory
from studio.jobs.events import EventBus
from studio.jobs.queue import JobQueue


class JobCancelledError(Exception):
    pass


class JobFailedError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class JobContext:
    """What a running job can see and do: report progress and notice cancellation."""

    def __init__(
        self,
        job: Job,
        sessions: SessionFactory,
        queue: JobQueue,
        events: EventBus,
        persist_interval: float = 0.25,
    ) -> None:
        self.job_id: uuid.UUID = job.id
        self.project_id: uuid.UUID = job.project_id
        self.kind = job.kind
        self.scope: dict[str, Any] = dict(job.scope)
        self.attempt = job.attempts
        self._sessions = sessions
        self._queue = queue
        self._events = events
        self._persist_interval = persist_interval
        self._last_persist = 0.0
        self.progress: dict[str, Any] = {"phase": "starting", "done": 0, "total": 0}
        self.stats: dict[str, Any] = {}

    async def is_cancelled(self) -> bool:
        return await self._queue.cancel_requested(str(self.job_id))

    async def check_cancelled(self) -> None:
        if await self.is_cancelled():
            raise JobCancelledError

    async def report(self, phase: str, done: int, total: int, *, force: bool = False, **stats: Any) -> None:
        self.progress = {"phase": phase, "done": done, "total": total}
        self.stats.update(stats)
        await self._events.publish(
            self.project_id,
            {
                "type": "job",
                "id": str(self.job_id),
                "kind": self.kind.value,
                "status": JobStatus.RUNNING.value,
                "attempts": self.attempt,
                "progress": self.progress,
                "stats": self.stats,
                "result": {},
                "error": None,
            },
        )
        now = time.monotonic()
        if force or now - self._last_persist >= self._persist_interval:
            self._last_persist = now
            async with self._sessions() as session, session.begin():
                await session.execute(
                    update(Job)
                    .where(
                        Job.id == self.job_id, Job.attempts == self.attempt, Job.status == JobStatus.RUNNING
                    )
                    .values(progress=self.progress, stats=self.stats)
                )

    async def publish(self, events: list[dict[str, Any]]) -> None:
        await self._events.publish_many(self.project_id, events)
