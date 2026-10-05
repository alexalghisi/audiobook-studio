"""The job worker process.

Each claimed job runs under a lease that a heartbeat keeps alive. Every state
change is written with the attempt number as a fencing token, so a worker
that lost its lease (a long GC pause, a network partition) can't overwrite
the outcome of the worker that took the job over.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from studio.db.models import ACTIVE_JOB_STATUSES, Job, JobStatus
from studio.db.session import SessionFactory
from studio.jobs.context import JobCancelledError, JobContext, JobFailedError
from studio.jobs.events import EventBus
from studio.jobs.handlers import Handler
from studio.jobs.queue import JobQueue
from studio.services.jobs import job_event

log = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        sessions: SessionFactory,
        queue: JobQueue,
        events: EventBus,
        handlers: dict[Any, Handler],
        *,
        worker_id: str | None = None,
        concurrency: int = 2,
        poll_seconds: float = 1.0,
        retry_base_seconds: float = 2.0,
        reconcile_after: timedelta = timedelta(seconds=30),
    ) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._sessions = sessions
        self._queue = queue
        self._events = events
        self._handlers = handlers
        self._concurrency = concurrency
        self._poll = poll_seconds
        self._retry_base = retry_base_seconds
        self._reconcile_after = reconcile_after
        self._running: dict[str, asyncio.Task[None]] = {}

    async def run(self, stop: asyncio.Event) -> None:
        await self._queue.heartbeat(self.worker_id, [])
        maintenance = asyncio.create_task(self._maintenance_loop(stop))
        log.info("worker %s started", self.worker_id)
        try:
            while not stop.is_set():
                if len(self._running) >= self._concurrency:
                    await asyncio.wait(set(self._running.values()), return_when=asyncio.FIRST_COMPLETED)
                    continue
                job_id = await self._queue.claim(self.worker_id, self._poll)
                if job_id is None:
                    continue
                task = asyncio.create_task(self._process(job_id))
                self._running[job_id] = task
                task.add_done_callback(self._forget)
        finally:
            if self._running:
                # Graceful stop: let jobs finish their current step. Anything left
                # stays in our processing list and is recovered by another worker.
                await asyncio.wait(set(self._running.values()), timeout=30)
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)
            await self._queue.release_worker(self.worker_id)
            log.info("worker %s stopped", self.worker_id)

    def _forget(self, task: asyncio.Task[None]) -> None:
        for job_id, running in list(self._running.items()):
            if running is task:
                del self._running[job_id]

    async def _maintenance_loop(self, stop: asyncio.Event) -> None:
        interval = self._queue.lease_ms / 3000
        last_reap = 0.0
        while not stop.is_set():
            try:
                await self._queue.heartbeat(self.worker_id, list(self._running))
                await self._queue.promote_due()
                if time.monotonic() - last_reap > interval * 3:
                    last_reap = time.monotonic()
                    recovered = await self._queue.requeue_orphans()
                    if recovered:
                        log.warning("requeued %d job(s) from dead workers: %s", len(recovered), recovered)
                    await self._reconcile()
            except Exception:
                log.exception("maintenance pass failed")
            await asyncio.sleep(interval)

    async def _reconcile(self) -> None:
        """Enqueues active jobs that Redis has lost track of (e.g. the API died after commit)."""
        cutoff = datetime.now(UTC) - self._reconcile_after
        async with self._sessions() as session:
            rows = await session.execute(
                select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES), Job.updated_at < cutoff)
            )
            candidates = [str(r[0]) for r in rows]
        for job_id in candidates:
            if not await self._queue.is_known(job_id):
                log.warning("re-enqueueing lost job %s", job_id)
                await self._queue.enqueue(job_id)

    async def _process(self, job_id: str) -> None:
        if not await self._queue.acquire_lease(self.worker_id, job_id):
            # Delivered twice and another live worker has it. Drop our copy only.
            await self._queue.drop(self.worker_id, job_id)
            return
        try:
            job = await self._start(job_id)
            if job is not None:
                await self._execute(job)
        except Exception:
            log.exception("job %s crashed the worker loop", job_id)
        finally:
            await self._queue.ack(self.worker_id, job_id)

    async def _start(self, job_id: str) -> Job | None:
        cancelled: Job | None = None
        async with self._sessions() as session, session.begin():
            job = await session.get(Job, uuid.UUID(job_id), with_for_update=True)
            if job is None or job.status not in ACTIVE_JOB_STATUSES:
                return None
            if job.cancel_requested:
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now(UTC)
                cancelled = job
            else:
                job.status = JobStatus.RUNNING
                job.attempts += 1
                job.started_at = job.started_at or datetime.now(UTC)
                job.error = None
        if cancelled is not None:
            await self._events.publish(cancelled.project_id, job_event(cancelled))
            return None
        return job

    async def _execute(self, job: Job) -> None:
        ctx = JobContext(job, self._sessions, self._queue, self._events)
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise JobFailedError(f"no handler for job kind {job.kind}", retryable=False)
            await self._events.publish(job.project_id, job_event(job))
            result = await handler(ctx)
        except JobCancelledError:
            await self._finish(ctx, JobStatus.CANCELLED, error=None)
        except JobFailedError as exc:
            await self._fail(ctx, job, str(exc), retryable=exc.retryable)
        except Exception as exc:
            log.exception("job %s failed unexpectedly", job.id)
            await self._fail(ctx, job, f"{exc.__class__.__name__}: {exc}", retryable=True)
        else:
            await self._finish(ctx, JobStatus.SUCCEEDED, error=None, result=result)

    async def _fail(self, ctx: JobContext, job: Job, message: str, *, retryable: bool) -> None:
        if retryable and job.attempts < job.max_attempts:
            delay = self._retry_base * 2 ** (job.attempts - 1)
            log.info("job %s attempt %d failed (%s); retrying in %.1fs", job.id, job.attempts, message, delay)
            if await self._finish(ctx, JobStatus.QUEUED, error=message):
                await self._queue.enqueue_at(str(job.id), time.time() + delay)
        else:
            await self._finish(ctx, JobStatus.FAILED, error=message)

    async def _finish(
        self, ctx: JobContext, status: JobStatus, *, error: str | None, result: dict[str, Any] | None = None
    ) -> bool:
        values: dict[str, Any] = {"status": status, "error": error, "stats": ctx.stats}
        if result is not None:
            values["result"] = result
        if status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
            values["finished_at"] = datetime.now(UTC)
            values["progress"] = {**ctx.progress, "phase": status.value}
        else:
            values["progress"] = {**ctx.progress, "phase": "retrying"}
        async with self._sessions() as session, session.begin():
            job = await session.scalar(
                update(Job)
                .where(Job.id == ctx.job_id, Job.attempts == ctx.attempt, Job.status == JobStatus.RUNNING)
                .values(**values)
                .returning(Job)
            )
        if job is None:
            log.warning("job %s attempt %d lost its lease; not recording %s", ctx.job_id, ctx.attempt, status)
            return False
        await self._events.publish(job.project_id, job_event(job))
        return True


async def _main() -> None:
    from studio.runtime import Runtime

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    runtime = Runtime.from_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await runtime.worker().run(stop)
    finally:
        await runtime.aclose()


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
