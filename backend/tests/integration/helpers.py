from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select

from studio.db.models import Chapter, Job, JobKind, JobStatus, Segment
from studio.domain.manuscript import parse_manuscript
from studio.jobs.worker import Worker
from studio.runtime import Runtime
from studio.testing.books import make_markdown

TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


async def import_book(rt: Runtime, chapters: int = 2, paragraphs: int = 3, sentences: int = 2) -> uuid.UUID:
    book = parse_manuscript("book.md", make_markdown(chapters, paragraphs, sentences).encode())
    return await rt.projects.import_book(book)


async def segments(rt: Runtime, project_id: uuid.UUID) -> list[Segment]:
    async with rt.sessions() as session:
        rows = await session.execute(
            select(Segment)
            .join(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.position, Segment.position)
        )
        return list(rows.scalars())


async def statuses(rt: Runtime, project_id: uuid.UUID) -> list[str]:
    return [s.status.value for s in await segments(rt, project_id)]


@asynccontextmanager
async def running_worker(rt: Runtime, worker: Worker | None = None) -> AsyncIterator[Worker]:
    worker = worker or rt.worker(retry_base_seconds=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    try:
        yield worker
    finally:
        stop.set()
        await asyncio.wait_for(task, 30)


async def wait_for_job(
    rt: Runtime, job_id: uuid.UUID, until: set[JobStatus] | None = None, timeout: float = 60
) -> Job:
    until = until or TERMINAL
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        job = await rt.jobs.get(job_id)
        assert job is not None
        if job.status in until:
            return job
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"job stuck in {job.status} ({job.progress}, error={job.error})")
        await asyncio.sleep(0.05)


async def synthesize_all(rt: Runtime, project_id: uuid.UUID) -> Job:
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all")
    async with running_worker(rt):
        return await wait_for_job(rt, job.id)
