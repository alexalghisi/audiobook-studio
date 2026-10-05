"""Job lifecycle against real PostgreSQL and Redis: dedupe, idempotency, cancel, retry, recovery."""

from __future__ import annotations

import asyncio

import pytest

from studio.db.models import JobKind, JobStatus, SegmentStatus
from studio.runtime import Runtime
from studio.tts.base import ProviderError

from ..conftest import CountingProvider
from .helpers import import_book, running_worker, segments, statuses, wait_for_job

pytestmark = pytest.mark.integration


async def test_active_job_is_deduplicated(rt: Runtime) -> None:
    project_id = await import_book(rt)
    first, created_first = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all")
    second, created_second = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all")
    assert (created_first, created_second) == (True, False)
    assert second.id == first.id

    async with running_worker(rt):
        await wait_for_job(rt, first.id)
    third, created_third = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all")
    assert created_third
    assert third.id != first.id


async def test_concurrent_submits_create_one_job(rt: Runtime) -> None:
    project_id = await import_book(rt)
    results = await asyncio.gather(
        *(rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all") for _ in range(8))
    )
    assert len({job.id for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1


async def test_idempotency_key_returns_the_original_job_even_when_finished(rt: Runtime) -> None:
    project_id = await import_book(rt)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="a", idempotency_key="click-1")
    async with running_worker(rt):
        await wait_for_job(rt, job.id)
    again, created = await rt.jobs.submit(
        project_id, JobKind.SYNTHESIZE, dedupe_key="a", idempotency_key="click-1"
    )
    assert (again.id, created, again.status) == (job.id, False, JobStatus.SUCCEEDED)


async def test_cancelling_a_queued_job_means_it_never_runs(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    await rt.jobs.cancel(job.id)
    async with running_worker(rt):
        await asyncio.sleep(0.5)
    assert (await wait_for_job(rt, job.id)).status == JobStatus.CANCELLED
    assert provider.texts == []


async def test_cancelling_a_running_job_stops_synthesis(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt, chapters=3, paragraphs=4)
    total = len(await segments(rt, project_id))
    provider.delay = 0.2
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    async with running_worker(rt):
        while len(provider.texts) < 2:
            await asyncio.sleep(0.02)
        await rt.jobs.cancel(job.id)
        final = await wait_for_job(rt, job.id)

    assert final.status == JobStatus.CANCELLED
    assert len(provider.texts) < total
    # Nothing is left looking busy: segments are either done or back to pending.
    assert set(await statuses(rt, project_id)) <= {"cached", "pending"}


async def test_transient_failures_are_retried_without_redoing_finished_segments(
    rt: Runtime, provider: CountingProvider
) -> None:
    project_id = await import_book(rt)
    segs = await segments(rt, project_id)
    flaky = segs[2].text
    provider.fail_texts[flaky] = ProviderError("HTTP 503", retryable=True)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")

    async with running_worker(rt):
        while (current := await rt.jobs.get(job.id)) is not None and current.error is None:
            await asyncio.sleep(0.02)  # until the first attempt has failed
        provider.fail_texts.clear()
        final = await wait_for_job(rt, job.id)

    assert (final.status, final.attempts) == (JobStatus.SUCCEEDED, 2)
    assert provider.texts.count(flaky) == 2
    assert sorted(set(provider.texts)) == sorted({s.text for s in segs})
    assert len(provider.texts) == len({s.text for s in segs}) + 1


async def test_permanent_failure_fails_the_job_once(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt)
    bad = (await segments(rt, project_id))[1]
    provider.fail_texts[bad.text] = ProviderError("HTTP 422 invalid text", retryable=False)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    async with running_worker(rt):
        final = await wait_for_job(rt, job.id)

    assert (final.status, final.attempts) == (JobStatus.FAILED, 1)
    assert final.error == f"1 of {len(await segments(rt, project_id))} segments failed to synthesize"
    failed = [s for s in await segments(rt, project_id) if s.status == SegmentStatus.FAILED]
    assert [(s.id, s.error) for s in failed] == [(bad.id, "HTTP 422 invalid text")]


async def test_jobs_of_a_dead_worker_are_recovered(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    # A worker claims the job and dies before doing anything (no heartbeat, no lease).
    claimed = await rt.queue.claim("ghost", 1)
    assert claimed == str(job.id)
    assert (await rt.queue.depth())["ready"] == 0

    async with running_worker(rt):
        final = await wait_for_job(rt, job.id, timeout=20)
    assert final.status == JobStatus.SUCCEEDED


async def test_job_lost_by_redis_is_reconciled_from_postgres(rt: Runtime) -> None:
    from datetime import timedelta

    from studio.jobs.worker import Worker

    project_id = await import_book(rt)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    await rt.redis.delete(rt.queue.ready_key)  # e.g. Redis restarted without persistence

    worker = Worker(
        rt.sessions,
        rt.queue,
        rt.events,
        rt.handlers(),
        poll_seconds=0.1,
        reconcile_after=timedelta(0),
    )
    async with running_worker(rt, worker):
        final = await wait_for_job(rt, job.id, timeout=20)
    assert final.status == JobStatus.SUCCEEDED


async def test_progress_events_are_published(rt: Runtime) -> None:
    project_id = await import_book(rt)
    received: list[dict[str, object]] = []

    async def listen() -> None:
        async with rt.events.subscription(project_id) as sub:
            while True:
                event = await sub.get(0.05)
                if event is not None:
                    received.append(event)
                    if event.get("type") == "job" and event.get("status") == "succeeded":
                        return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    async with running_worker(rt):
        await asyncio.wait_for(listener, 30)

    kinds = [(e["type"], e.get("status")) for e in received]
    assert ("job", "queued") in kinds
    assert ("job", "running") in kinds
    assert ("segment", "generating") in kinds
    assert ("segment", "cached") in kinds
    done_values = [e["progress"]["done"] for e in received if e["type"] == "job" and e["status"] == "running"]  # type: ignore[index]
    assert done_values == sorted(done_values)


async def test_worker_survives_redis_errors_while_claiming(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import TimeoutError as RedisTimeout

    project_id = await import_book(rt, chapters=1, paragraphs=1)
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="all")
    real_claim = rt.queue.claim
    failures = {"left": 2}

    async def flaky_claim(worker_id: str, wait_seconds: float) -> str | None:
        if failures["left"]:
            failures["left"] -= 1
            raise RedisTimeout("Timeout reading from localhost:6379")
        return await real_claim(worker_id, wait_seconds)

    monkeypatch.setattr(rt.queue, "claim", flaky_claim)
    async with running_worker(rt):
        final = await wait_for_job(rt, job.id, timeout=20)
    assert final.status == JobStatus.SUCCEEDED
    assert failures["left"] == 0
