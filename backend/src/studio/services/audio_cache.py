"""Segment audio cache: content-addressed files plus an index row per key."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from studio.audio.ffmpeg import decode_pcm
from studio.audio.peaks import PEAK_RATE, buckets_for, compute_peaks
from studio.audio.store import ContentStore
from studio.db.models import AudioBlob
from studio.db.session import SessionFactory
from studio.tts.base import SynthesisResult


@dataclass(frozen=True, slots=True)
class CachedAudio:
    key: str
    format: str
    duration_ms: int
    path: Path


class AudioCache:
    def __init__(self, store: ContentStore, sessions: SessionFactory) -> None:
        self._store = store
        self._sessions = sessions

    def path(self, key: str, fmt: str) -> Path:
        return self._store.path(key, fmt)

    async def get(self, key: str) -> CachedAudio | None:
        found = await self.get_many([key])
        return found.get(key)

    async def get_many(self, keys: Iterable[str]) -> dict[str, CachedAudio]:
        wanted = list(set(keys))
        if not wanted:
            return {}
        async with self._sessions() as session:
            rows = (await session.execute(select(AudioBlob).where(AudioBlob.key.in_(wanted)))).scalars()
            found: dict[str, CachedAudio] = {}
            for row in rows:
                # The row is written after the file, but a file can still go missing
                # (disk cleanup, restored backup). Treat that as a miss, not an error.
                if self._store.exists(row.key, row.format):
                    found[row.key] = CachedAudio(
                        row.key, row.format, row.duration_ms, self._store.path(row.key, row.format)
                    )
            return found

    async def put(self, key: str, result: SynthesisResult, provider: str) -> CachedAudio:
        pcm = await decode_pcm(result.audio, PEAK_RATE)
        duration_ms = round(pcm.size * 1000 / PEAK_RATE)
        peaks = compute_peaks(pcm, buckets_for(duration_ms, per_second=40))
        path = self._store.write(key, result.format, result.audio)
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(AudioBlob)
                .values(
                    key=key,
                    format=result.format,
                    duration_ms=duration_ms,
                    size_bytes=len(result.audio),
                    peaks=peaks,
                    provider=provider,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
        return CachedAudio(key, result.format, duration_ms, path)

    async def peaks(self, key: str) -> list[float] | None:
        async with self._sessions() as session:
            row = await session.get(AudioBlob, key)
            return None if row is None else list(row.peaks)
