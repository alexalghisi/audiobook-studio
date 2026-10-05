"""Benchmark: export a 20-chapter book offline, edit one paragraph, export again.

Runs the real pipeline (PostgreSQL, Redis, worker, espeak-ng, ffmpeg) in one
process against a throwaway database and data directory.

    python bench/render_book.py --chapters 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from studio.config import Settings
from studio.db.models import Base, Chapter, Job, JobKind, JobStatus, Segment
from studio.db.session import make_engine, make_session_factory
from studio.domain.manuscript import parse_manuscript
from studio.runtime import Runtime
from studio.testing.books import make_markdown
from studio.tts.base import SynthesisRequest, SynthesisResult, TTSProvider, Voice
from studio.tts.offline import offline_provider

ROOT = Path(__file__).resolve().parent
DB = os.environ.get(
    "BENCH_DATABASE_URL", "postgresql+asyncpg://synckit:synckit@localhost:5432/audiobook_studio_bench"
)


class Counting:
    def __init__(self, inner: TTSProvider) -> None:
        self.inner = inner
        self.requests = 0
        self.name = inner.name
        self.default_voice_id = inner.default_voice_id
        self.default_model_id = inner.default_model_id
        self.max_concurrency = inner.max_concurrency

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.requests += 1
        return await self.inner.synthesize(request)

    async def list_voices(self) -> list[Voice]:
        return await self.inner.list_voices()

    async def aclose(self) -> None:
        await self.inner.aclose()


async def fresh_database() -> None:
    base, name = DB.rsplit("/", 1)
    admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    engine = create_async_engine(DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def export(rt: Runtime, project_id: uuid.UUID) -> tuple[float, Job]:
    started = time.perf_counter()
    job, _ = await rt.jobs.submit(project_id, JobKind.EXPORT, dedupe_key="export")
    stop = asyncio.Event()
    worker = asyncio.create_task(rt.worker(concurrency=1).run(stop))
    while True:
        current = await rt.jobs.get(job.id)
        assert current is not None
        if current.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
            break
        await asyncio.sleep(0.05)
    elapsed = time.perf_counter() - started
    stop.set()
    await worker
    assert current.status == JobStatus.SUCCEEDED, current.error
    return elapsed, current


def summarize(label: str, seconds: float, job: Job, requests: int) -> dict[str, Any]:
    synthesis = job.result["synthesis"]
    chapters = job.result["chapters"]
    lufs = [c["integrated_lufs"] for c in chapters]
    return {
        "run": label,
        "seconds": round(seconds, 1),
        "phases": job.result["timings"],
        "segments": synthesis["segments"],
        "synthesis_requests": requests,
        "cache_hits": synthesis["cache_hits"],
        "cache_hit_rate": round(synthesis["cache_hits"] / synthesis["segments"], 4),
        "chapters_rendered": sum(not c["cached"] for c in chapters),
        "chapters_reused": sum(c["cached"] for c in chapters),
        "book_repackaged": not job.result["book"]["cached"],
        "audio_minutes": round(job.result["book"]["duration_ms"] / 60000, 1),
        "lufs_range": [min(lufs), max(lufs)],
        "max_true_peak": max(c["true_peak_dbtp"] for c in chapters),
    }


async def main(chapters: int, paragraphs: int, sentences: int) -> None:
    await fresh_database()
    data = ROOT / "out" / "data"
    shutil.rmtree(data, ignore_errors=True)
    settings = Settings(
        database_url=DB, data_dir=data, queue_prefix=f"bench-{uuid.uuid4().hex[:6]}", elevenlabs_api_key=None
    )
    provider = Counting(offline_provider())
    engine = make_engine(DB)
    rt = Runtime(settings, engine, make_session_factory(engine), Redis.from_url(settings.redis_url), provider)

    markdown = make_markdown(chapters, paragraphs, sentences, seed=42, title="Benchmark Book")
    started = time.perf_counter()
    project_id = await rt.projects.import_book(parse_manuscript("bench.md", markdown.encode()))
    import_seconds = time.perf_counter() - started

    results = []
    seconds, job = await export(rt, project_id)
    results.append(summarize("first export", seconds, job, provider.requests))

    async with rt.sessions() as session:
        target = await session.scalar(
            select(Segment).join(Chapter)
            .where(Chapter.project_id == project_id, Chapter.position == chapters // 2)
            .order_by(Segment.position).limit(1)
        )  # fmt: skip
    assert target is not None
    await rt.projects.edit_segment(target.id, text=target.text.replace(" on day ", " once more on day "))
    provider.requests = 0
    seconds, job = await export(rt, project_id)
    results.append(summarize("after editing one paragraph", seconds, job, provider.requests))

    provider.requests = 0
    seconds, job = await export(rt, project_id)
    results.append(summarize("no changes", seconds, job, provider.requests))

    report = {
        "book": {"chapters": chapters, "words": len(markdown.split()), "characters": len(markdown)},
        "provider": provider.name,
        "cpus": os.cpu_count(),
        "import_seconds": round(import_seconds, 2),
        "runs": results,
    }
    print(json.dumps(report, indent=2))
    (ROOT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    async for key in rt.redis.scan_iter(match=f"{settings.queue_prefix}:*"):
        await rt.redis.delete(key)
    await rt.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapters", type=int, default=20)
    parser.add_argument("--paragraphs", type=int, default=8)
    parser.add_argument("--sentences", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.chapters, args.paragraphs, args.sentences))
