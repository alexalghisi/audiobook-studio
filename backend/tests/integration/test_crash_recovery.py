"""kill -9 a worker process mid-job; another worker finishes without re-synthesizing cached audio."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Iterator

import pytest

from studio.config import Settings
from studio.db.models import JobKind, JobStatus, SegmentStatus
from studio.runtime import Runtime
from studio.testing.fake_elevenlabs import FakeElevenLabs
from studio.testing.server import serve
from studio.tts.elevenlabs import ElevenLabsProvider

from .helpers import import_book, running_worker, segments, wait_for_job

pytestmark = pytest.mark.integration


@pytest.fixture
def fake() -> FakeElevenLabs:
    return FakeElevenLabs(api_key="crash-test", latency_s=0.15)


@pytest.fixture
def fake_url(fake: FakeElevenLabs) -> Iterator[str]:
    with serve(fake.app()) as url:
        yield url


def _worker_env(settings: Settings, fake_url: str) -> dict[str, str]:
    return {
        **os.environ,
        "STUDIO_DATABASE_URL": settings.database_url,
        "STUDIO_REDIS_URL": settings.redis_url,
        "STUDIO_DATA_DIR": str(settings.data_dir),
        "STUDIO_QUEUE_PREFIX": settings.queue_prefix,
        "STUDIO_WORKER_LEASE_SECONDS": str(settings.worker_lease_seconds),
        "STUDIO_WORKER_POLL_SECONDS": "0.1",
        "STUDIO_ELEVENLABS_CONCURRENCY": "2",
        "ELEVENLABS_API_KEY": "crash-test",
        "STUDIO_ELEVENLABS_BASE_URL": fake_url,
    }


async def test_killed_worker_resumes_without_resynthesizing(
    rt: Runtime, settings: Settings, fake: FakeElevenLabs, fake_url: str
) -> None:
    eleven = ElevenLabsProvider("crash-test", base_url=fake_url, concurrency=2)
    rt = Runtime(settings=settings, engine=rt.engine, sessions=rt.sessions, redis=rt.redis, provider=eleven)
    project_id = await import_book(rt, chapters=3, paragraphs=4)
    total = len(await segments(rt, project_id))
    job, _ = await rt.jobs.submit(project_id, JobKind.SYNTHESIZE, dedupe_key="synthesize:all")

    proc = subprocess.Popen(
        [sys.executable, "-m", "studio.jobs.worker"],
        env=_worker_env(settings, fake_url),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while True:
            cached = [s for s in await segments(rt, project_id) if s.status == SegmentStatus.CACHED]
            if len(cached) >= total // 3:
                break
            assert proc.poll() is None, "worker exited early"
            await asyncio.sleep(0.05)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    cached_before = {s.text for s in await segments(rt, project_id) if s.status == SegmentStatus.CACHED}
    requests_before = len(fake.synthesized_texts)
    assert (await rt.jobs.get(job.id)).status == JobStatus.RUNNING  # type: ignore[union-attr]

    async with running_worker(rt):
        final = await wait_for_job(rt, job.id, timeout=60)

    assert final.status == JobStatus.SUCCEEDED
    assert final.attempts == 2
    after_kill = fake.synthesized_texts[requests_before:]
    assert not set(after_kill) & cached_before, "cached segments were synthesized again"
    unique = len({s.text for s in await segments(rt, project_id)})
    # At most the requests that were in flight at the moment of the kill are repeated.
    assert len(fake.synthesized_texts) <= unique + 2
    assert final.result["synthesis"]["cache_hits"] >= len(cached_before)
    await eleven.aclose()
