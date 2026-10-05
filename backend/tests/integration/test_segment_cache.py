"""Content-addressed caching end to end: PostgreSQL, Redis, a worker, real audio files."""

from __future__ import annotations

import pytest

from studio.db.models import JobStatus, SegmentStatus
from studio.domain.pronunciation import PronunciationRule
from studio.domain.voice import SegmentOverrides, VoiceSettings
from studio.runtime import Runtime

from ..conftest import CountingProvider
from .helpers import import_book, segments, statuses, synthesize_all

pytestmark = pytest.mark.integration


async def test_first_run_synthesizes_every_unique_segment_once(
    rt: Runtime, provider: CountingProvider
) -> None:
    project_id = await import_book(rt)
    segs = await segments(rt, project_id)
    assert set(await statuses(rt, project_id)) == {"pending"}

    job = await synthesize_all(rt, project_id)

    assert job.status == JobStatus.SUCCEEDED
    assert set(await statuses(rt, project_id)) == {"cached"}
    unique_texts = {s.text for s in segs}
    assert sorted(provider.texts) == sorted(unique_texts)
    stats = job.result["synthesis"]
    assert stats["segments"] == len(segs)
    assert stats["synthesized"] == len(unique_texts)
    assert stats["deduplicated"] == len(segs) - len(unique_texts)
    assert job.progress == {"phase": "succeeded", "done": len(segs), "total": len(segs)}


async def test_rerun_is_free(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt)
    await synthesize_all(rt, project_id)
    provider.texts.clear()

    job = await synthesize_all(rt, project_id)

    assert provider.texts == []
    assert job.result["synthesis"]["cache_hits"] == job.result["synthesis"]["segments"]


async def test_editing_one_paragraph_regenerates_only_that_segment(
    rt: Runtime, provider: CountingProvider
) -> None:
    project_id = await import_book(rt)
    await synthesize_all(rt, project_id)
    provider.texts.clear()
    target = (await segments(rt, project_id))[3]

    await rt.projects.edit_segment(target.id, text=target.text.replace(" on day ", " again on day "))

    after = {s.id: s for s in await segments(rt, project_id)}
    assert after[target.id].status == SegmentStatus.PENDING
    assert [s.status for sid, s in after.items() if sid != target.id] == [SegmentStatus.CACHED] * (
        len(after) - 1
    )

    job = await synthesize_all(rt, project_id)
    assert provider.texts == [after[target.id].text]
    assert job.result["synthesis"]["cache_hits"] == len(after) - 1


async def test_reverting_an_edit_is_instantly_cached(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt)
    await synthesize_all(rt, project_id)
    target = (await segments(rt, project_id))[0]

    await rt.projects.edit_segment(target.id, text="Something else entirely.")
    _, changes = await rt.projects.edit_segment(target.id, text=target.text)

    assert [c.status for c in changes] == [SegmentStatus.CACHED]


async def test_pronunciation_rule_invalidates_only_matching_segments(
    rt: Runtime, provider: CountingProvider
) -> None:
    project_id = await import_book(rt, chapters=2, paragraphs=6)
    await synthesize_all(rt, project_id)
    provider.texts.clear()
    segs = await segments(rt, project_id)
    matching = [s for s in segs if "Mara" in s.text]
    assert 0 < len(matching) < len(segs)

    changes = await rt.projects.replace_rules(project_id, [PronunciationRule("Mara", "MAH-rah")])

    assert {c.segment_id for c in changes} == {s.id for s in matching}
    await synthesize_all(rt, project_id)
    assert len(provider.texts) == len({s.text for s in matching})
    assert all("MAH-rah" in t and "Mara" not in t for t in provider.texts)


async def test_voice_override_and_new_take_change_the_address(
    rt: Runtime, provider: CountingProvider
) -> None:
    project_id = await import_book(rt)
    await synthesize_all(rt, project_id)
    first, second = (await segments(rt, project_id))[:2]

    _, a = await rt.projects.edit_segment(first.id, overrides=SegmentOverrides(speed=1.15))
    _, b = await rt.projects.edit_segment(second.id, new_take=True)
    assert [c.status for c in a + b] == [SegmentStatus.PENDING, SegmentStatus.PENDING]

    provider.texts.clear()
    await synthesize_all(rt, project_id)
    assert sorted(provider.texts) == sorted([first.text, second.text])


async def test_project_voice_change_invalidates_everything(rt: Runtime) -> None:
    project_id = await import_book(rt)
    await synthesize_all(rt, project_id)
    changes = await rt.projects.update_voice(project_id, settings=VoiceSettings(speed=0.9))
    assert len(changes) == len(await segments(rt, project_id))
    # Going back to the old settings finds the old audio.
    changes = await rt.projects.update_voice(project_id, settings=VoiceSettings())
    assert {c.status for c in changes} == {SegmentStatus.CACHED}


async def test_missing_audio_file_is_a_cache_miss(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt, chapters=1, paragraphs=1)
    await synthesize_all(rt, project_id)
    for path in rt.settings.audio_dir.rglob("*.wav"):
        path.unlink()
    provider.texts.clear()
    job = await synthesize_all(rt, project_id)
    assert job.result["synthesis"]["cache_hits"] == 0
    assert provider.texts
