"""HTTP API against real PostgreSQL, Redis and an in-process worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from studio.api.main import create_app
from studio.config import Settings
from studio.runtime import Runtime
from studio.testing.books import make_markdown
from studio.testing.server import serve

from ..conftest import CountingProvider
from .helpers import running_worker

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(rt: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(rt))
    async with httpx.AsyncClient(transport=transport, base_url="http://studio") as c:
        yield c


async def _import(client: httpx.AsyncClient, chapters: int = 2) -> dict[str, Any]:
    md = make_markdown(chapters, 3, 2)
    r = await client.post("/api/projects", files={"file": ("salt.md", md.encode(), "text/markdown")})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def _wait_job(client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    for _ in range(600):
        job: dict[str, Any] = (await client.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("job did not finish")


async def test_import_returns_chapters_with_pending_segments(client: httpx.AsyncClient) -> None:
    project = await _import(client)
    assert project["title"] == "Salt and Light"
    assert project["provider"] == "offline-formant"
    assert [c["title"] for c in project["chapters"]] == ["Chapter 1", "Chapter 2"]
    chapter = project["chapters"][0]
    assert chapter["status_counts"] == {"pending": chapter["segment_count"]}

    listed = (await client.get("/api/projects")).json()
    assert [(p["id"], p["chapter_count"]) for p in listed] == [(project["id"], 2)]


async def test_rejects_unsupported_manuscripts(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/projects", files={"file": ("a.pdf", b"%PDF-1.7", "application/pdf")})
    assert r.status_code == 422
    assert "unsupported" in r.json()["detail"]


async def test_synthesize_edit_and_regenerate_flow(
    client: httpx.AsyncClient, rt: Runtime, provider: CountingProvider
) -> None:
    project = await _import(client)
    async with running_worker(rt):
        r = await client.post(f"/api/projects/{project['id']}/synthesize")
        assert r.status_code == 202
        assert (await _wait_job(client, r.json()["id"]))["status"] == "succeeded"

        chapter_id = project["chapters"][0]["id"]
        segments = (await client.get(f"/api/chapters/{chapter_id}/segments")).json()
        assert {s["status"] for s in segments} == {"cached"}
        assert all(s["duration_ms"] > 0 for s in segments)

        audio = await client.get(f"/api/segments/{segments[0]['id']}/audio")
        assert audio.status_code == 200
        assert audio.headers["content-type"] == "audio/wav"
        assert audio.content[:4] == b"RIFF"
        peaks = (await client.get(f"/api/segments/{segments[0]['id']}/peaks")).json()
        assert len(peaks["peaks"]) >= 32
        assert max(peaks["peaks"]) <= 1.0

        target = segments[1]
        edited = await client.patch(f"/api/segments/{target['id']}", json={"text": "A brand new line."})
        assert edited.json()["status"] == "pending"
        assert (await client.get(f"/api/segments/{target['id']}/audio")).status_code == 404

        provider.texts.clear()
        job = (await client.post(f"/api/segments/{target['id']}/regenerate")).json()
        assert (await _wait_job(client, job["id"]))["result"]["synthesis"]["synthesized"] == 1
        assert provider.texts == ["A brand new line."]

        provider.texts.clear()
        job = (await client.post(f"/api/segments/{target['id']}/regenerate?new_take=true")).json()
        await _wait_job(client, job["id"])
        assert provider.texts == ["A brand new line."]


async def test_segment_override_can_be_set_and_cleared(client: httpx.AsyncClient) -> None:
    project = await _import(client)
    chapter_id = project["chapters"][0]["id"]
    seg = (await client.get(f"/api/chapters/{chapter_id}/segments")).json()[0]

    with_override = (await client.patch(f"/api/segments/{seg['id']}", json={"speed": 1.1})).json()
    assert with_override["speed"] == 1.1
    assert with_override["cache_key"] != seg["cache_key"]
    cleared = (await client.patch(f"/api/segments/{seg['id']}", json={"speed": None})).json()
    assert cleared["speed"] is None
    assert cleared["cache_key"] == seg["cache_key"]

    bad = await client.patch(f"/api/segments/{seg['id']}", json={"speed": 3})
    assert bad.status_code == 422


async def test_pronunciations_and_voice_settings(client: httpx.AsyncClient) -> None:
    project = await _import(client)
    r = await client.put(
        f"/api/projects/{project['id']}/pronunciations",
        json={"rules": [{"term": "Mara", "alias": "MAH-rah"}]},
    )
    assert r.json()["rules"] == [
        {"term": "Mara", "alias": "MAH-rah", "case_sensitive": False, "whole_word": True}
    ]
    dup = await client.put(
        f"/api/projects/{project['id']}/pronunciations",
        json={"rules": [{"term": "x", "alias": "a"}, {"term": "x", "alias": "b"}]},
    )
    assert dup.status_code == 422

    r = await client.patch(f"/api/projects/{project['id']}", json={"voice_settings": {"speed": 0.9}})
    assert r.json()["voice_settings"]["speed"] == 0.9


async def test_idempotency_key_and_dedupe(client: httpx.AsyncClient) -> None:
    project = await _import(client)
    url = f"/api/projects/{project['id']}/synthesize"
    first = await client.post(url, headers={"Idempotency-Key": "abc"})
    again = await client.post(url, headers={"Idempotency-Key": "abc"})
    deduped = await client.post(url)
    assert (first.status_code, again.status_code, deduped.status_code) == (202, 200, 200)
    assert first.json()["id"] == again.json()["id"] == deduped.json()["id"]

    cancelled = (await client.post(f"/api/jobs/{first.json()['id']}/cancel")).json()
    assert cancelled["status"] == "cancelled"


async def test_unknown_ids_are_404(client: httpx.AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    for url in (f"/api/projects/{missing}", f"/api/chapters/{missing}/segments", f"/api/jobs/{missing}"):
        assert (await client.get(url)).status_code == 404


async def test_event_stream_relays_progress(rt: Runtime, settings: Settings) -> None:
    # The served app runs on its own event loop, so it gets its own runtime.
    app = create_app(runtime_factory=lambda: Runtime.from_settings(settings, CountingProvider()))
    with serve(app, lifespan=True) as base:
        async with httpx.AsyncClient(base_url=base, timeout=30) as client:
            project = await _import(client)
            events: list[tuple[str, dict[str, Any]]] = []

            async def read() -> None:
                async with client.stream("GET", f"/api/projects/{project['id']}/events") as stream:
                    name = ""
                    async for line in stream.aiter_lines():
                        if line.startswith("event: "):
                            name = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            events.append((name, data))
                            if name == "job" and data["status"] == "succeeded":
                                return

            reader = asyncio.create_task(read())
            await asyncio.sleep(0.3)
            async with running_worker(rt):
                await client.post(f"/api/projects/{project['id']}/synthesize")
                await asyncio.wait_for(reader, 30)

    assert events[0] == ("snapshot", {"jobs": []})
    names = {name for name, _ in events}
    assert {"job", "segment"} <= names
    assert any(d.get("status") == "generating" for n, d in events if n == "segment")


async def test_export_downloads_and_chapter_peaks(client: httpx.AsyncClient, rt: Runtime) -> None:
    project = await _import(client)
    async with running_worker(rt):
        r = await client.post(f"/api/projects/{project['id']}/export")
        job = await _wait_job(client, r.json()["id"])
    assert job["status"] == "succeeded", job["error"]

    book = job["result"]["book"]
    m4b = await client.get(f"/api/renders/{book['key']}.m4b", params={"name": "Salt and Light"})
    assert m4b.status_code == 200
    assert m4b.headers["content-type"] == "audio/mp4"
    assert m4b.headers["content-disposition"] == "attachment; filename*=utf-8''Salt%20and%20Light.m4b"

    chapter = job["result"]["chapters"][0]
    mp3 = await client.get(f"/api/renders/{chapter['key']}.mp3")
    assert mp3.headers["content-type"] == "audio/mpeg"
    peaks = (await client.get(f"/api/renders/{chapter['key']}/peaks")).json()
    assert peaks["duration_ms"] == chapter["duration_ms"]

    assert (await client.get(f"/api/renders/{chapter['key']}.wav")).status_code == 404
    assert (await client.get("/api/renders/..%2F..%2Fetc.mp3")).status_code == 404
