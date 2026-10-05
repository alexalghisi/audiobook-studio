"""The export job: synthesize what is missing, render chapters, package the book."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

from studio.db.models import Chapter, Project, Segment
from studio.db.session import SessionFactory
from studio.jobs.context import JobCancelledError, JobContext, JobFailedError
from studio.jobs.handlers import Handler, raise_on_failures
from studio.render.renderer import BookMeta, ChapterSpec, RenderedChapter, Renderer, SegmentAudio
from studio.services.audio_cache import AudioCache
from studio.services.synthesis import Synthesizer


async def _load_specs(
    sessions: SessionFactory, cache: AudioCache, ctx: JobContext
) -> tuple[BookMeta, list[tuple[Chapter, ChapterSpec]]]:
    async with sessions() as session:
        project = await session.get_one(Project, ctx.project_id)
        chapters = list(
            (
                await session.execute(
                    select(Chapter).where(Chapter.project_id == ctx.project_id).order_by(Chapter.position)
                )
            ).scalars()
        )
        rows = await session.execute(
            select(Segment)
            .join(Chapter)
            .where(Chapter.project_id == ctx.project_id)
            .order_by(Segment.position)
        )
        by_chapter: dict[Any, list[Segment]] = {}
        for segment in rows.scalars():
            by_chapter.setdefault(segment.chapter_id, []).append(segment)

    audio = await cache.get_many(s.cache_key for segs in by_chapter.values() for s in segs)
    specs: list[tuple[Chapter, ChapterSpec]] = []
    for chapter in chapters:
        segments = by_chapter.get(chapter.id, [])
        missing = [s for s in segments if s.cache_key not in audio]
        if missing:
            # Edited after synthesis finished; a retry synthesizes the new text.
            raise JobFailedError(f"{len(missing)} segments changed during the export", retryable=True)
        if segments:
            specs.append(
                (
                    chapter,
                    ChapterSpec(
                        title=chapter.title,
                        track=len(specs) + 1,
                        segments=[
                            SegmentAudio(s.cache_key, audio[s.cache_key].path, s.ends_paragraph)
                            for s in segments
                        ],
                    ),
                )
            )
    return BookMeta(project.title, project.author), specs


def export_handler(
    synthesizer: Synthesizer,
    sessions: SessionFactory,
    cache: AudioCache,
    renderer: Renderer,
    chapter_concurrency: int = 2,
) -> Handler:
    async def handle(ctx: JobContext) -> dict[str, Any]:
        clock = time.perf_counter()
        timings: dict[str, float] = {}

        def lap(phase: str) -> None:
            nonlocal clock
            now = time.perf_counter()
            timings[phase] = round(now - clock, 2)
            clock = now

        stats = await synthesizer.run(ctx)
        raise_on_failures(stats)
        lap("synthesis_s")

        book, specs = await _load_specs(sessions, cache, ctx)
        total = len(specs) + 1
        done = 0
        rendered: dict[int, RenderedChapter] = {}
        slots = asyncio.Semaphore(chapter_concurrency)

        async def render(index: int, spec: ChapterSpec) -> None:
            nonlocal done
            async with slots:
                await ctx.check_cancelled()
                rendered[index] = await renderer.render_chapter(spec, book, len(specs))
            done += 1
            await ctx.report(
                "rendering", done, total,
                render={"chapters": len(specs), "cached": sum(r.cached for r in rendered.values())},
            )  # fmt: skip

        await ctx.report("rendering", 0, total, force=True)
        try:
            async with asyncio.TaskGroup() as group:
                for index, (_, spec) in enumerate(specs):
                    group.create_task(render(index, spec))
        except BaseExceptionGroup as group_error:
            # Report the cause, not the TaskGroup wrapper.
            if group_error.subgroup(JobCancelledError) is not None:
                raise JobCancelledError from None
            raise group_error.exceptions[0] from None

        lap("render_s")
        await ctx.check_cancelled()
        await ctx.report("packaging", done, total, force=True)
        chapters = [(spec.title, rendered[i]) for i, (_, spec) in enumerate(specs)]
        audiobook = await renderer.render_book(chapters, book)
        await ctx.report("packaging", total, total, force=True)
        lap("package_s")
        return {
            "synthesis": stats.as_dict(),
            "timings": timings,
            "chapters": [
                {
                    "chapter_id": str(chapter.id),
                    "title": spec.title,
                    "key": rendered[i].key,
                    "duration_ms": rendered[i].duration_ms,
                    "integrated_lufs": rendered[i].integrated_lufs,
                    "true_peak_dbtp": rendered[i].true_peak_dbtp,
                    "cached": rendered[i].cached,
                }
                for i, (chapter, spec) in enumerate(specs)
            ],
            "book": {
                "key": audiobook.key,
                "duration_ms": audiobook.duration_ms,
                "chapter_starts_ms": audiobook.chapter_starts_ms,
                "cached": audiobook.cached,
            },
        }

    return handle
