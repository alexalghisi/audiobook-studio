"""Rendering checked with ffprobe and an independent EBU R128 measurement."""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.audio.ffmpeg import probe
from studio.audio.store import ContentStore
from studio.domain.voice import SegmentOverrides, VoiceSettings, resolve_voice
from studio.render.loudness import measure
from studio.render.renderer import BookMeta, ChapterSpec, Pauses, Renderer, SegmentAudio
from studio.tts.base import SynthesisRequest
from studio.tts.offline import FormantProvider

BOOK = BookMeta(title="Salt and Light", author="A. Writer")
VOICE = resolve_voice("narrator-us-m", "m", VoiceSettings(), SegmentOverrides())


async def _segments(tmp: Path, texts: list[str], quiet: bool = False) -> list[SegmentAudio]:
    provider = FormantProvider()
    out = []
    for i, text in enumerate(texts):
        audio = (await provider.synthesize(SynthesisRequest(text, VOICE))).audio
        path = tmp / f"seg{i}.wav"
        path.write_bytes(audio)
        if quiet:
            # Re-encode 20 dB quieter to prove normalization brings it up.
            from studio.audio.ffmpeg import ffmpeg

            loud = path.with_suffix(".loud.wav")
            path.rename(loud)
            await ffmpeg("-i", str(loud), "-af", "volume=-20dB", str(path))
        out.append(SegmentAudio(key=f"{i:064d}", path=path, ends_paragraph=i % 2 == 1))
    return out


TEXTS = [
    "The keeper climbed the stairs, as he did every night.",
    "He counted them, all ninety nine.",
    "The lamp was cold when he reached the top.",
    "Nobody had told him the ship was coming early.",
]


@pytest.fixture
def renderer(tmp_path: Path) -> Renderer:
    return Renderer(ContentStore(tmp_path / "renders"))


async def test_chapter_mp3_is_normalized_tagged_and_paced(renderer: Renderer, tmp_path: Path) -> None:
    segments = await _segments(tmp_path, TEXTS, quiet=True)
    chapter = await renderer.render_chapter(ChapterSpec("Chapter One", 1, segments), BOOK, total_tracks=3)

    info = await probe(chapter.path)
    stream = info["streams"][0]
    assert (stream["codec_name"], stream["sample_rate"], stream["channels"]) == ("mp3", "44100", 1)
    assert 185_000 <= int(info["format"]["bit_rate"]) <= 200_000
    tags = {k.lower(): v for k, v in info["format"]["tags"].items()}
    assert (tags["title"], tags["album"], tags["artist"], tags["track"]) == (
        "Chapter One",
        "Salt and Light",
        "A. Writer",
        "1/3",
    )

    loudness = await measure(chapter.path)
    assert loudness.integrated == pytest.approx(-18.0, abs=1.0)
    assert loudness.true_peak <= -2.5
    assert chapter.integrated_lufs == pytest.approx(loudness.integrated, abs=1.0)

    pauses = Pauses()
    from studio.audio.wav import wav_duration_ms

    speech_ms = sum(wav_duration_ms(s.path.read_bytes()) for s in segments)
    gaps = pauses.head_ms + pauses.tail_ms + 2 * pauses.sentence_ms + pauses.paragraph_ms
    assert chapter.duration_ms == pytest.approx(speech_ms + gaps, abs=120)


async def test_silences_sit_where_the_pauses_are(renderer: Renderer, tmp_path: Path) -> None:
    from studio.audio.ffmpeg import ffmpeg_stderr

    segments = await _segments(tmp_path, TEXTS[:2])
    chapter = await renderer.render_chapter(ChapterSpec("One", 1, segments), BOOK, 1)
    stderr = await ffmpeg_stderr(
        "-i", str(chapter.path), "-af", "silencedetect=noise=-50dB:d=0.75", "-f", "null", "-"
    )
    # Head (0.6 s) is too short for d=0.75; the 2 s tail is the one long silence.
    assert stderr.count("silence_start") == 1


async def test_rerender_with_same_inputs_is_a_cache_hit(renderer: Renderer, tmp_path: Path) -> None:
    segments = await _segments(tmp_path, TEXTS[:2])
    first = await renderer.render_chapter(ChapterSpec("One", 1, segments), BOOK, 1)
    second = await renderer.render_chapter(ChapterSpec("One", 1, segments), BOOK, 1)
    assert (first.cached, second.cached) == (False, True)
    assert second.key == first.key

    retitled = await renderer.render_chapter(ChapterSpec("Uno", 1, segments), BOOK, 1)
    assert not retitled.cached


async def test_interrupted_render_is_not_mistaken_for_a_finished_one(
    renderer: Renderer, tmp_path: Path
) -> None:
    segments = await _segments(tmp_path, TEXTS[:1])
    first = await renderer.render_chapter(ChapterSpec("One", 1, segments), BOOK, 1)
    renderer.path(first.key, "json").unlink()  # as if the process died before the sidecar was written
    again = await renderer.render_chapter(ChapterSpec("One", 1, segments), BOOK, 1)
    assert not again.cached


async def test_m4b_has_one_marker_per_chapter(renderer: Renderer, tmp_path: Path) -> None:
    segments = await _segments(tmp_path, TEXTS)
    titles = ["Prologue", "The Lamp", "Fog = Weather; #3"]
    chapters = [
        (title, await renderer.render_chapter(ChapterSpec(title, i + 1, segments[i : i + 2]), BOOK, 3))
        for i, title in enumerate(titles)
    ]
    book = await renderer.render_book(chapters, BOOK)

    info = await probe(book.path)
    assert info["streams"][0]["codec_name"] == "aac"
    assert [c["tags"]["title"] for c in info["chapters"]] == titles
    starts = [round(float(c["start_time"]) * 1000) for c in info["chapters"]]
    assert starts == pytest.approx(book.chapter_starts_ms, abs=60)
    assert float(info["format"]["duration"]) * 1000 == pytest.approx(book.duration_ms, abs=250)
    assert info["format"]["tags"]["title"] == "Salt and Light"

    again = await renderer.render_book(chapters, BOOK)
    assert again.cached
