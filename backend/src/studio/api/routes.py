from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, select

from studio.api import queries
from studio.api.schemas import (
    HealthOut,
    JobOut,
    PeaksOut,
    ProjectDetail,
    ProjectPatch,
    ProjectSummary,
    RulesPut,
    SegmentOut,
    SegmentPatch,
    SynthesizeRequest,
    VoiceOut,
)
from studio.db.models import ACTIVE_JOB_STATUSES, Job, JobKind, Project, Segment
from studio.domain.manuscript import ManuscriptError, parse_manuscript
from studio.domain.pronunciation import PronunciationRule
from studio.domain.voice import SegmentOverrides
from studio.runtime import Runtime
from studio.services.jobs import job_event
from studio.services.projects import InvalidEditError, NotFoundError, SegmentChange

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SSE_KEEPALIVE_SECONDS = 15.0

router = APIRouter(prefix="/api")


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def _publish_changes(rt: Runtime, project_id: uuid.UUID, changes: list[SegmentChange]) -> None:
    await rt.events.publish_many(project_id, [c.event() for c in changes])


async def _detail(rt: Runtime, project_id: uuid.UUID) -> ProjectDetail:
    async with rt.sessions() as session:
        detail = await queries.project_detail(session, project_id, rt.provider.name)
    if detail is None:
        raise _not_found("project")
    return detail


@router.get("/health")
async def health(rt: RuntimeDep) -> HealthOut:
    return HealthOut(status="ok", provider=rt.provider.name, queue=await rt.queue.depth())


@router.get("/voices")
async def voices(rt: RuntimeDep) -> list[VoiceOut]:
    return [
        VoiceOut(voice_id=v.voice_id, name=v.name, description=v.description)
        for v in await rt.provider.list_voices()
    ]


@router.get("/projects")
async def list_projects(rt: RuntimeDep) -> list[ProjectSummary]:
    async with rt.sessions() as session:
        return await queries.list_projects(session)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def import_project(rt: RuntimeDep, file: Annotated[UploadFile, File()]) -> ProjectDetail:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "manuscripts are limited to 20 MB")
    try:
        book = await asyncio.to_thread(parse_manuscript, file.filename or "manuscript.txt", data)
    except ManuscriptError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    project_id = await rt.projects.import_book(book)
    return await _detail(rt, project_id)


@router.get("/projects/{project_id}")
async def get_project(rt: RuntimeDep, project_id: uuid.UUID) -> ProjectDetail:
    return await _detail(rt, project_id)


@router.patch("/projects/{project_id}")
async def patch_project(rt: RuntimeDep, project_id: uuid.UUID, body: ProjectPatch) -> ProjectDetail:
    try:
        changes = await rt.projects.update_voice(
            project_id,
            voice_id=body.voice_id,
            model_id=body.model_id,
            settings=body.voice_settings,
            title=body.title,
        )
    except NotFoundError as exc:
        raise _not_found("project") from exc
    await _publish_changes(rt, project_id, changes)
    return await _detail(rt, project_id)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(rt: RuntimeDep, project_id: uuid.UUID) -> Response:
    async with rt.sessions() as session, session.begin():
        active = await session.scalar(
            select(Job.id).where(Job.project_id == project_id, Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1)
        )
        if active is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "cancel the running jobs first")
        result = await session.execute(delete(Project).where(Project.id == project_id).returning(Project.id))
        if result.first() is None:
            raise _not_found("project")
    # Audio stays in the content store: another project may share it.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/projects/{project_id}/pronunciations")
async def put_rules(rt: RuntimeDep, project_id: uuid.UUID, body: RulesPut) -> ProjectDetail:
    rules = [PronunciationRule(r.term, r.alias, r.case_sensitive, r.whole_word) for r in body.rules]
    try:
        changes = await rt.projects.replace_rules(project_id, rules)
    except NotFoundError as exc:
        raise _not_found("project") from exc
    except InvalidEditError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _publish_changes(rt, project_id, changes)
    return await _detail(rt, project_id)


@router.get("/chapters/{chapter_id}/segments")
async def list_segments(rt: RuntimeDep, chapter_id: uuid.UUID) -> list[SegmentOut]:
    async with rt.sessions() as session:
        segments = await queries.chapter_segments(session, chapter_id)
    if segments is None:
        raise _not_found("chapter")
    return segments


@router.patch("/segments/{segment_id}")
async def patch_segment(rt: RuntimeDep, segment_id: uuid.UUID, body: SegmentPatch) -> SegmentOut:
    fields = body.model_fields_set
    overrides = None
    if fields & {"voice_id", "speed", "stability"}:
        async with rt.sessions() as session:
            current = await session.get(Segment, segment_id)
        if current is None:
            raise _not_found("segment")
        overrides = SegmentOverrides(
            voice_id=body.voice_id if "voice_id" in fields else current.voice_id,
            speed=body.speed if "speed" in fields else current.speed,
            stability=body.stability if "stability" in fields else current.stability,
        )
    try:
        project_id, changes = await rt.projects.edit_segment(
            segment_id, text=body.text if "text" in fields else None, overrides=overrides
        )
    except NotFoundError as exc:
        raise _not_found("segment") from exc
    except InvalidEditError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _publish_changes(rt, project_id, changes)
    async with rt.sessions() as session:
        out = await queries.segment_out(session, segment_id)
    assert out is not None
    return out


async def _submit(
    rt: RuntimeDep,
    project_id: uuid.UUID,
    kind: JobKind,
    dedupe_key: str,
    scope: dict[str, Any],
    idempotency_key: str | None,
    response: Response,
) -> JobOut:
    job, created = await rt.jobs.submit(
        project_id, kind, dedupe_key=dedupe_key, scope=scope, idempotency_key=idempotency_key
    )
    response.status_code = status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK
    return JobOut.model_validate(job)


IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


@router.post("/segments/{segment_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
async def regenerate_segment(
    rt: RuntimeDep,
    segment_id: uuid.UUID,
    response: Response,
    new_take: bool = False,
    idempotency_key: IdempotencyKey = None,
) -> JobOut:
    try:
        project_id, changes = await rt.projects.edit_segment(segment_id, new_take=new_take)
    except NotFoundError as exc:
        raise _not_found("segment") from exc
    await _publish_changes(rt, project_id, changes)
    async with rt.sessions() as session:
        segment = await session.get_one(Segment, segment_id)
    return await _submit(
        rt,
        project_id,
        JobKind.SYNTHESIZE,
        f"segment:{segment_id}:{segment.cache_key}",
        {"segment_ids": [str(segment_id)]},
        idempotency_key,
        response,
    )


@router.post("/projects/{project_id}/synthesize", status_code=status.HTTP_202_ACCEPTED)
async def synthesize(
    rt: RuntimeDep,
    project_id: uuid.UUID,
    response: Response,
    body: SynthesizeRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> JobOut:
    scope: dict[str, Any] = {}
    dedupe = "synthesize:all"
    async with rt.sessions() as session:
        if await session.get(Project, project_id) is None:
            raise _not_found("project")
        if body is not None and body.chapter_id is not None:
            ids = (
                await session.execute(select(Segment.id).where(Segment.chapter_id == body.chapter_id))
            ).scalars()
            scope = {"segment_ids": [str(i) for i in ids]}
            if not scope["segment_ids"]:
                raise _not_found("chapter")
            dedupe = f"synthesize:chapter:{body.chapter_id}"
    return await _submit(rt, project_id, JobKind.SYNTHESIZE, dedupe, scope, idempotency_key, response)


@router.get("/segments/{segment_id}/audio")
async def segment_audio(rt: RuntimeDep, segment_id: uuid.UUID) -> FileResponse:
    async with rt.sessions() as session:
        segment = await session.get(Segment, segment_id)
    if segment is None:
        raise _not_found("segment")
    cached = await rt.cache.get(segment.cache_key)
    if cached is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this segment has no audio yet")
    media = "audio/mpeg" if cached.format == "mp3" else "audio/wav"
    # Content-addressed: the URL changes whenever the audio does, so it can be cached forever.
    return FileResponse(
        cached.path, media_type=media, headers={"Cache-Control": "private, max-age=31536000, immutable"}
    )


@router.get("/segments/{segment_id}/peaks")
async def segment_peaks(rt: RuntimeDep, segment_id: uuid.UUID) -> PeaksOut:
    async with rt.sessions() as session:
        segment = await session.get(Segment, segment_id)
    if segment is None:
        raise _not_found("segment")
    cached = await rt.cache.get(segment.cache_key)
    peaks = await rt.cache.peaks(segment.cache_key) if cached else None
    if cached is None or peaks is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this segment has no audio yet")
    return PeaksOut(duration_ms=cached.duration_ms, peaks=peaks)


@router.get("/projects/{project_id}/jobs")
async def project_jobs(rt: RuntimeDep, project_id: uuid.UUID) -> list[JobOut]:
    return [JobOut.model_validate(j) for j in await rt.jobs.list_for_project(project_id)]


@router.get("/jobs/{job_id}")
async def get_job(rt: RuntimeDep, job_id: uuid.UUID) -> JobOut:
    job = await rt.jobs.get(job_id)
    if job is None:
        raise _not_found("job")
    return JobOut.model_validate(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(rt: RuntimeDep, job_id: uuid.UUID) -> JobOut:
    job = await rt.jobs.cancel(job_id)
    if job is None:
        raise _not_found("job")
    return JobOut.model_validate(job)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/projects/{project_id}/events")
async def project_events(rt: RuntimeDep, project_id: uuid.UUID, request: Request) -> StreamingResponse:
    async with rt.sessions() as session:
        if await session.get(Project, project_id) is None:
            raise _not_found("project")

    async def stream() -> AsyncIterator[bytes]:
        async with rt.events.subscription(project_id) as sub:
            # Subscribe first, then snapshot: an event can arrive twice but never get lost.
            yield b"retry: 2000\n\n"
            async with rt.sessions() as session:
                active = (
                    await session.execute(
                        select(Job).where(Job.project_id == project_id, Job.status.in_(ACTIVE_JOB_STATUSES))
                    )
                ).scalars()
                yield _sse("snapshot", {"jobs": [job_event(j) for j in active]})
            idle = 0.0
            while not await request.is_disconnected():
                event = await sub.get(1.0)
                if event is None:
                    idle += 1.0
                    if idle >= SSE_KEEPALIVE_SECONDS:
                        idle = 0.0
                        yield b": keep-alive\n\n"
                    continue
                idle = 0.0
                yield _sse(str(event.get("type", "message")), event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
