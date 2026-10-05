"""Chapter and book rendering.

A chapter is rendered in three steps: segment audio is decoded and stitched
with pauses (short between sentences of a paragraph, longer between
paragraphs, room tone at head and tail), the result is loudness normalized in
two passes, and it is encoded as a 192 kbps CBR MP3 with ID3 tags. The book
is the chapter MP3s concatenated into an AAC M4B with a chapter marker each.

Every output is stored under a key derived from its inputs (segment audio
keys, pauses, loudness target, tags). Rendering a chapter whose key already
exists is a no-op, which is what makes re-running an export cheap and lets
an interrupted export continue where it stopped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from studio.audio.ffmpeg import SAMPLE_RATE, decode_pcm, ffmpeg, ffmpeg_stderr, probe
from studio.audio.peaks import PEAK_RATE, buckets_for, compute_peaks
from studio.audio.store import ContentStore
from studio.audio.wav import pcm_to_wav
from studio.render.loudness import LoudnessTarget, analyze, second_pass_filter

RENDER_VERSION = 1


@dataclass(frozen=True, slots=True)
class Pauses:
    head_ms: int = 600
    sentence_ms: int = 300
    paragraph_ms: int = 800
    tail_ms: int = 2000


@dataclass(frozen=True, slots=True)
class SegmentAudio:
    key: str
    path: Path
    ends_paragraph: bool


@dataclass(frozen=True, slots=True)
class ChapterSpec:
    title: str
    track: int
    segments: Sequence[SegmentAudio]


@dataclass(frozen=True, slots=True)
class BookMeta:
    title: str
    author: str | None


@dataclass(frozen=True, slots=True)
class RenderedChapter:
    key: str
    path: Path
    duration_ms: int
    integrated_lufs: float
    true_peak_dbtp: float
    cached: bool


@dataclass(frozen=True, slots=True)
class RenderedBook:
    key: str
    path: Path
    duration_ms: int
    chapter_starts_ms: list[int] = field(default_factory=list)
    cached: bool = False


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class Renderer:
    def __init__(
        self,
        store: ContentStore,
        target: LoudnessTarget | None = None,
        pauses: Pauses | None = None,
        bitrate: str = "192k",
        decode_concurrency: int = 4,
    ) -> None:
        self._store = store
        self._target = target or LoudnessTarget()
        self._pauses = pauses or Pauses()
        self._bitrate = bitrate
        self._decoders = asyncio.Semaphore(decode_concurrency)

    def chapter_key(self, spec: ChapterSpec, book: BookMeta) -> str:
        return _digest(
            {
                "v": RENDER_VERSION,
                "kind": "chapter",
                "target": asdict(self._target),
                "pauses": asdict(self._pauses),
                "bitrate": self._bitrate,
                "title": spec.title,
                "track": spec.track,
                "book": asdict(book),
                "segments": [(s.key, s.ends_paragraph) for s in spec.segments],
            }
        )

    def book_key(self, chapters: Sequence[tuple[str, str]], book: BookMeta) -> str:
        return _digest(
            {"v": RENDER_VERSION, "kind": "book", "book": asdict(book), "chapters": list(chapters)}
        )

    def path(self, key: str, ext: str) -> Path:
        return self._store.path(key, ext)

    async def render_chapter(self, spec: ChapterSpec, book: BookMeta, total_tracks: int) -> RenderedChapter:
        key = self.chapter_key(spec, book)
        meta_path = self._store.path(key, "json")
        if self._store.exists(key, "mp3") and meta_path.is_file():
            meta = json.loads(meta_path.read_text())
            return RenderedChapter(
                key, self._store.path(key, "mp3"), meta["duration_ms"], meta["integrated_lufs"],
                meta["true_peak_dbtp"], cached=True,
            )  # fmt: skip

        pcm = await self._stitch(spec)
        self._store.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._store.root, prefix=".render-") as tmp:
            stitched = Path(tmp) / "stitched.wav"
            stitched.write_bytes(pcm_to_wav(pcm, SAMPLE_RATE))
            measured = await analyze(stitched, self._target)
            out = Path(tmp) / "chapter.mp3"
            stderr = await ffmpeg_stderr(
                "-i", str(stitched),
                "-af", second_pass_filter(self._target, measured),
                "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", self._bitrate,
                "-id3v2_version", "3",
                "-metadata", f"title={spec.title}",
                "-metadata", f"album={book.title}",
                "-metadata", f"artist={book.author or ''}",
                "-metadata", f"track={spec.track}/{total_tracks}",
                "-metadata", "genre=Audiobook",
                str(out),
            )  # fmt: skip
            result = json.loads(stderr[stderr.rfind("{") : stderr.rfind("}") + 1])
            duration_ms = round(float((await probe(out))["format"]["duration"]) * 1000)
            meta = {
                "duration_ms": duration_ms,
                "integrated_lufs": float(result["output_i"]),
                "true_peak_dbtp": float(result["output_tp"]),
                "normalization": result.get("normalization_type"),
            }
            peaks_pcm = await decode_pcm(out, PEAK_RATE)
            meta["peaks"] = compute_peaks(peaks_pcm, buckets_for(duration_ms, per_second=10))
            # The JSON sidecar is written last: its presence marks the render as complete.
            path = self._store.adopt(key, "mp3", out)
        self._store.write(key, "json", json.dumps(meta).encode())
        return RenderedChapter(
            key, path, duration_ms, meta["integrated_lufs"], meta["true_peak_dbtp"], cached=False
        )

    def chapter_meta(self, key: str) -> dict[str, object] | None:
        path = self._store.path(key, "json")
        if not path.is_file():
            return None
        meta: dict[str, object] = json.loads(path.read_text())
        return meta

    async def _decode_segment(self, segment: SegmentAudio) -> npt.NDArray[np.int16]:
        async with self._decoders:
            return await decode_pcm(segment.path, SAMPLE_RATE)

    async def _stitch(self, spec: ChapterSpec) -> npt.NDArray[np.int16]:
        decoded = await asyncio.gather(*(self._decode_segment(s) for s in spec.segments))

        def silence(ms: int) -> npt.NDArray[np.int16]:
            return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.int16)

        parts = [silence(self._pauses.head_ms)]
        for i, (segment, pcm) in enumerate(zip(spec.segments, decoded, strict=True)):
            parts.append(pcm)
            if i < len(spec.segments) - 1:
                parts.append(
                    silence(self._pauses.paragraph_ms if segment.ends_paragraph else self._pauses.sentence_ms)
                )
        parts.append(silence(self._pauses.tail_ms))
        return np.concatenate(parts)

    async def render_book(
        self, chapters: Sequence[tuple[str, RenderedChapter]], book: BookMeta
    ) -> RenderedBook:
        key = self.book_key([(title, ch.key) for title, ch in chapters], book)
        starts: list[int] = []
        position = 0
        for _, chapter in chapters:
            starts.append(position)
            position += chapter.duration_ms
        if self._store.exists(key, "m4b"):
            return RenderedBook(key, self._store.path(key, "m4b"), position, starts, cached=True)

        self._store.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._store.root, prefix=".book-") as tmp:
            concat = Path(tmp) / "chapters.txt"
            concat.write_text("".join(f"file '{ch.path.resolve()}'\n" for _, ch in chapters))
            metadata = Path(tmp) / "chapters.ffmeta"
            metadata.write_text(_ffmetadata(chapters, starts, book))
            out = Path(tmp) / "book.m4b"
            await ffmpeg(
                "-f", "concat", "-safe", "0", "-i", str(concat),
                "-i", str(metadata),
                "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
                "-c:a", "aac", "-b:a", "64k", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-movflags", "+faststart", "-f", "mp4",
                str(out),
            )  # fmt: skip
            path = self._store.adopt(key, "m4b", out)
        return RenderedBook(key, path, position, starts, cached=False)


def _escape(value: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        value = value.replace(ch, f"\\{ch}")
    return value


def _ffmetadata(chapters: Sequence[tuple[str, RenderedChapter]], starts: list[int], book: BookMeta) -> str:
    lines = [
        ";FFMETADATA1",
        f"title={_escape(book.title)}",
        f"album={_escape(book.title)}",
        "genre=Audiobook",
    ]
    if book.author:
        lines += [f"artist={_escape(book.author)}", f"album_artist={_escape(book.author)}"]
    for (title, chapter), start in zip(chapters, starts, strict=True):
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={start + chapter.duration_ms}",
            f"title={_escape(title)}",
        ]
    return "\n".join(lines) + "\n"
