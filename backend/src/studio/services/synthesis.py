"""Synthesizes the segments of a project that have no audio yet.

For each segment in scope the content address is recomputed from the current
text and settings. Addresses already in the cache cost nothing. The remaining
ones are deduplicated (two segments with identical text and voice need one
request) and synthesized with bounded concurrency. Results are written to the
cache first and the segment row second, and the row update is conditional on
the segment still having that address, so an edit made while a request was in
flight is never overwritten with stale audio.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import select, update

from studio.db.models import Chapter, Segment, SegmentStatus
from studio.db.session import SessionFactory
from studio.jobs.context import JobCancelledError, JobContext
from studio.services.audio_cache import AudioCache
from studio.services.projects import load_voice_context
from studio.tts.base import ProviderError, SynthesisRequest, TTSProvider

log = logging.getLogger(__name__)

CANCEL_POLL_SECONDS = 0.25


@dataclass(slots=True)
class SynthesisStats:
    segments: int = 0
    cache_hits: int = 0
    synthesized: int = 0
    deduplicated: int = 0
    failed: int = 0
    retryable_failures: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Work:
    key: str
    request: SynthesisRequest
    segment_ids: list[uuid.UUID]


class Synthesizer:
    def __init__(self, sessions: SessionFactory, cache: AudioCache, provider: TTSProvider) -> None:
        self._sessions = sessions
        self._cache = cache
        self._provider = provider
        # Shared by every job in this process, so concurrent jobs can't exceed the provider's limit.
        self._slots = asyncio.Semaphore(provider.max_concurrency)
        self._inflight: dict[str, asyncio.Future[None]] = {}

    async def run(self, ctx: JobContext, segment_ids: list[uuid.UUID] | None = None) -> SynthesisStats:
        stats = SynthesisStats()
        work, hits = await self._plan(ctx, segment_ids, stats)
        await self._set_status(ctx, hits, SegmentStatus.CACHED)
        queued = {sid: item.key for item in work for sid in item.segment_ids}
        await self._set_status(ctx, queued, SegmentStatus.QUEUED)
        done = stats.cache_hits
        await ctx.report("synthesizing", done, stats.segments, force=True, synthesis=stats.as_dict())

        async def process(item: _Work) -> None:
            nonlocal done
            try:
                # The slot is taken here, not around the HTTP call alone, so "generating"
                # means a request is really in flight and the rest show as queued.
                async with self._slots:
                    await self._set_status(
                        ctx, dict.fromkeys(item.segment_ids, item.key), SegmentStatus.GENERATING
                    )
                    await self._synthesize(item)
            except Exception as exc:
                if not isinstance(exc, ProviderError):
                    log.exception("unexpected error synthesizing %s", item.key)
                stats.failed += len(item.segment_ids)
                if not isinstance(exc, ProviderError) or exc.retryable:
                    stats.retryable_failures += len(item.segment_ids)
                message = str(exc) or exc.__class__.__name__
                await self._set_status(
                    ctx, dict.fromkeys(item.segment_ids, item.key), SegmentStatus.FAILED, message
                )
            else:
                stats.synthesized += 1
                await self._set_status(ctx, dict.fromkeys(item.segment_ids, item.key), SegmentStatus.CACHED)
            done += len(item.segment_ids)
            await ctx.report("synthesizing", done, stats.segments, synthesis=stats.as_dict())

        batch = asyncio.ensure_future(asyncio.gather(*(process(item) for item in work)))
        try:
            while not batch.done():
                await asyncio.wait({batch}, timeout=CANCEL_POLL_SECONDS)
                if not batch.done() and await ctx.is_cancelled():
                    batch.cancel()
                    await asyncio.gather(batch, return_exceptions=True)
                    raise JobCancelledError
            batch.result()
        except BaseException:
            await self._reset_unfinished(ctx, queued)
            raise
        await ctx.report("synthesizing", done, stats.segments, force=True, synthesis=stats.as_dict())
        return stats

    async def _plan(
        self, ctx: JobContext, segment_ids: list[uuid.UUID] | None, stats: SynthesisStats
    ) -> tuple[list[_Work], dict[uuid.UUID, str]]:
        wanted = set(segment_ids) if segment_ids is not None else None
        async with self._sessions() as session:
            voice_ctx = await load_voice_context(session, ctx.project_id, self._provider.name)
            rows = await session.execute(
                select(Segment)
                .join(Chapter)
                .where(Chapter.project_id == ctx.project_id)
                .order_by(Chapter.position, Segment.position)
            )
            segments = list(rows.scalars())

        keys = {s.id: voice_ctx.key_for(s) for s in segments}
        in_scope = [s for s in segments if wanted is None or s.id in wanted]
        stats.segments = len(in_scope)
        cached = await self._cache.get_many(keys[s.id] for s in in_scope)

        hits: dict[uuid.UUID, str] = {}
        by_key: dict[str, list[Segment]] = defaultdict(list)
        for s in in_scope:
            if keys[s.id] in cached:
                hits[s.id] = keys[s.id]
            else:
                by_key[keys[s.id]].append(s)
        stats.cache_hits = len(hits)

        neighbours: dict[uuid.UUID, tuple[str | None, str | None]] = {}
        for i, s in enumerate(segments):
            prev = segments[i - 1] if i > 0 and segments[i - 1].chapter_id == s.chapter_id else None
            nxt = (
                segments[i + 1]
                if i + 1 < len(segments) and segments[i + 1].chapter_id == s.chapter_id
                else None
            )
            neighbours[s.id] = (
                voice_ctx.spoken_text(prev.text) if prev else None,
                voice_ctx.spoken_text(nxt.text) if nxt else None,
            )

        work: list[_Work] = []
        for key, group in by_key.items():
            first = group[0]
            previous_text, next_text = neighbours[first.id]
            request = SynthesisRequest(
                text=voice_ctx.spoken_text(first.text),
                voice=voice_ctx.voice_for(first),
                previous_text=previous_text,
                next_text=next_text,
            )
            stats.deduplicated += len(group) - 1
            work.append(_Work(key, request, [s.id for s in group]))
        return work, hits

    async def _synthesize(self, item: _Work) -> None:
        # Another job in this process may already be producing the same audio.
        while (shared := self._inflight.get(item.key)) is not None:
            try:
                await asyncio.shield(shared)
                return
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                # Only the job that owned the request was cancelled: produce it here.
        produce = asyncio.ensure_future(self._produce(item))
        self._inflight[item.key] = produce
        try:
            await produce
        finally:
            if self._inflight.get(item.key) is produce:
                del self._inflight[item.key]

    async def _produce(self, item: _Work) -> None:
        result = await self._provider.synthesize(item.request)
        await self._cache.put(item.key, result, self._provider.name)

    async def _set_status(
        self,
        ctx: JobContext,
        segments: dict[uuid.UUID, str],
        status: SegmentStatus,
        error: str | None = None,
    ) -> None:
        if not segments:
            return
        by_key: dict[str, list[uuid.UUID]] = defaultdict(list)
        for sid, key in segments.items():
            by_key[key].append(sid)
        changed: list[tuple[uuid.UUID, uuid.UUID]] = []
        async with self._sessions() as session, session.begin():
            for key, ids in by_key.items():
                rows = await session.execute(
                    update(Segment)
                    .where(Segment.id.in_(ids), Segment.cache_key == key)
                    .values(status=status, error=error)
                    .returning(Segment.id, Segment.chapter_id)
                )
                changed.extend((row[0], row[1]) for row in rows)
        await ctx.publish(
            [
                {
                    "type": "segment",
                    "id": str(sid),
                    "chapter_id": str(chapter_id),
                    "status": status.value,
                    "cache_key": segments[sid],
                    "error": error,
                }
                for sid, chapter_id in changed
            ]
        )

    async def _reset_unfinished(self, ctx: JobContext, queued: dict[uuid.UUID, str]) -> None:
        """After a cancel or an unexpected error, segments that never got audio go back to pending."""
        if not queued:
            return
        async with self._sessions() as session:
            rows = await session.execute(
                select(Segment.id, Segment.cache_key).where(
                    Segment.id.in_(list(queued)),
                    Segment.status.in_([SegmentStatus.QUEUED, SegmentStatus.GENERATING]),
                )
            )
            unfinished = {row[0]: row[1] for row in rows}
        await asyncio.shield(self._set_status(ctx, unfinished, SegmentStatus.PENDING))
