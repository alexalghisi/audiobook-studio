"""The export job end to end: synthesis, chapter rendering, M4B packaging and their caches."""

from __future__ import annotations

from typing import Any

import pytest

from studio.audio.ffmpeg import probe
from studio.db.models import JobKind, JobStatus
from studio.render.loudness import measure
from studio.render.renderer import Renderer
from studio.runtime import Runtime

from ..conftest import CountingProvider
from .helpers import import_book, running_worker, segments, wait_for_job

pytestmark = pytest.mark.integration


async def export(rt: Runtime, project_id: Any) -> Any:
    job, _ = await rt.jobs.submit(project_id, JobKind.EXPORT, dedupe_key="export")
    async with running_worker(rt):
        return await wait_for_job(rt, job.id, timeout=120)


async def test_export_produces_normalized_chapters_and_a_marked_m4b(rt: Runtime) -> None:
    project_id = await import_book(rt, chapters=3, paragraphs=2)
    job = await export(rt, project_id)

    assert job.status == JobStatus.SUCCEEDED, job.error
    chapters = job.result["chapters"]
    assert [c["title"] for c in chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    for chapter in chapters:
        loudness = await measure(rt.renders.path(chapter["key"], "mp3"))
        assert loudness.integrated == pytest.approx(-18, abs=1)
        assert loudness.true_peak <= -2.5

    info = await probe(rt.renders.path(job.result["book"]["key"], "m4b"))
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert job.progress["phase"] == "succeeded"


async def test_editing_one_paragraph_rerenders_one_chapter(rt: Runtime, provider: CountingProvider) -> None:
    project_id = await import_book(rt, chapters=3, paragraphs=2)
    first = await export(rt, project_id)
    provider.texts.clear()

    target = (await segments(rt, project_id))[-1]  # last chapter
    await rt.projects.edit_segment(target.id, text="The fog came in early that year.")
    second = await export(rt, project_id)

    assert provider.texts == ["The fog came in early that year."]
    assert second.result["synthesis"]["synthesized"] == 1
    assert [c["cached"] for c in second.result["chapters"]] == [True, True, False]
    assert second.result["book"]["cached"] is False
    assert second.result["book"]["key"] != first.result["book"]["key"]

    third = await export(rt, project_id)
    assert all(c["cached"] for c in third.result["chapters"])
    assert third.result["book"]["cached"] is True
    assert provider.texts == ["The fog came in early that year."]


async def test_failed_render_resumes_from_finished_chapters(
    rt: Runtime, provider: CountingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await import_book(rt, chapters=3, paragraphs=2)
    real = Renderer.render_chapter
    calls = {"n": 0}

    async def crash_on_third(self: Renderer, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("ffmpeg was killed")
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(Renderer, "render_chapter", crash_on_third)
    unique = len({s.text for s in await segments(rt, project_id)})
    job = await export(rt, project_id)

    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)
    assert calls["n"] == 3 + 3
    assert len(provider.texts) == unique  # nothing synthesized twice
    assert job.result["synthesis"]["synthesized"] == 0
    # Chapters finished before the crash are reused (two render concurrently, so at least one).
    assert sum(c["cached"] for c in job.result["chapters"]) >= 1
