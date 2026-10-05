"""Submitting and cancelling jobs (the API side of the job system)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from studio.db.models import ACTIVE_JOB_STATUSES, Job, JobKind, JobStatus
from studio.db.session import SessionFactory
from studio.jobs.events import EventBus
from studio.jobs.queue import JobQueue


def job_event(job: Job) -> dict[str, Any]:
    return {
        "type": "job",
        "id": str(job.id),
        "kind": job.kind.value,
        "status": job.status.value,
        "attempts": job.attempts,
        "progress": job.progress,
        "stats": job.stats,
        "result": job.result,
        "error": job.error,
    }


class JobService:
    def __init__(
        self, sessions: SessionFactory, queue: JobQueue, events: EventBus, max_attempts: int = 3
    ) -> None:
        self._sessions = sessions
        self._queue = queue
        self._events = events
        self._max_attempts = max_attempts

    async def submit(
        self,
        project_id: uuid.UUID,
        kind: JobKind,
        *,
        dedupe_key: str,
        scope: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[Job, bool]:
        """Creates and enqueues a job, or returns the existing one.

        Returns (job, created). A repeated idempotency key returns the job it
        created the first time, whatever its status. Otherwise, an active job
        with the same dedupe key is returned instead of starting a second one.
        """
        async with self._sessions() as session:
            if idempotency_key:
                existing = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
                if existing is not None and existing.project_id == project_id:
                    return existing, False
            job = Job(
                project_id=project_id,
                kind=kind,
                dedupe_key=dedupe_key,
                idempotency_key=idempotency_key,
                scope=scope or {},
                max_attempts=self._max_attempts,
                progress={"phase": "queued", "done": 0, "total": 0},
                stats={},
                result={},
            )
            session.add(job)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                active = await session.scalar(
                    select(Job).where(
                        Job.project_id == project_id,
                        Job.dedupe_key == dedupe_key,
                        Job.status.in_(ACTIVE_JOB_STATUSES),
                    )
                )
                if active is None and idempotency_key:
                    # Lost a race on the idempotency key itself.
                    active = await session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
                if active is None:
                    raise
                return active, False
        # The row is committed before the job is enqueued. If this process dies in
        # between, the workers' reconcile pass enqueues it.
        await self._queue.enqueue(str(job.id))
        await self._events.publish(project_id, job_event(job))
        return job, True

    async def cancel(self, job_id: uuid.UUID) -> Job | None:
        async with self._sessions() as session, session.begin():
            job = await session.get(Job, job_id, with_for_update=True)
            if job is None:
                return None
            if job.status in ACTIVE_JOB_STATUSES:
                job.cancel_requested = True
                if job.status == JobStatus.QUEUED:
                    # Nobody is working on it: cancel now. A worker that still
                    # dequeues it sees the terminal status and drops it.
                    job.status = JobStatus.CANCELLED
                    job.progress = {**job.progress, "phase": "cancelled"}
        await self._queue.request_cancel(str(job_id))
        await self._events.publish(job.project_id, job_event(job))
        return job

    async def get(self, job_id: uuid.UUID) -> Job | None:
        async with self._sessions() as session:
            return await session.get(Job, job_id)

    async def list_for_project(self, project_id: uuid.UUID, limit: int = 20) -> list[Job]:
        async with self._sessions() as session:
            rows = await session.execute(
                select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(limit)
            )
            return list(rows.scalars())
