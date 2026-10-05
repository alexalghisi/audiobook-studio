"""Read-side queries that shape rows for the API."""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.api.schemas import ChapterOut, JobOut, ProjectDetail, ProjectSummary, RuleOut, SegmentOut
from studio.db.models import (
    ACTIVE_JOB_STATUSES,
    AudioBlob,
    Chapter,
    Job,
    Project,
    PronunciationRuleRow,
    Segment,
    SegmentStatus,
)
from studio.domain.voice import VoiceSettings


async def list_projects(session: AsyncSession) -> list[ProjectSummary]:
    chapter_count = (
        select(func.count(Chapter.id))
        .where(Chapter.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )
    segment_count = (
        select(func.count(Segment.id))
        .join(Chapter)
        .where(Chapter.project_id == Project.id)
        .correlate(Project)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Project, chapter_count, segment_count).order_by(Project.created_at.desc())
    )
    return [
        ProjectSummary(
            id=p.id,
            title=p.title,
            author=p.author,
            chapter_count=chapters,
            segment_count=segments,
            created_at=p.created_at,
        )
        for p, chapters, segments in rows
    ]


async def project_detail(session: AsyncSession, project_id: uuid.UUID, provider: str) -> ProjectDetail | None:
    project = await session.get(Project, project_id)
    if project is None:
        return None
    chapters = list(
        (
            await session.execute(
                select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.position)
            )
        ).scalars()
    )
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for chapter_id, status, n in await session.execute(
        select(Segment.chapter_id, Segment.status, func.count())
        .join(Chapter)
        .where(Chapter.project_id == project_id)
        .group_by(Segment.chapter_id, Segment.status)
    ):
        counts[chapter_id][status.value] = n
    duration_rows = await session.execute(
        select(Segment.chapter_id, func.coalesce(func.sum(AudioBlob.duration_ms), 0))
        .join(Chapter)
        .join(AudioBlob, AudioBlob.key == Segment.cache_key)
        .where(Chapter.project_id == project_id, Segment.status == SegmentStatus.CACHED)
        .group_by(Segment.chapter_id)
    )
    durations: dict[uuid.UUID, int] = {row[0]: int(row[1]) for row in duration_rows}
    rules = (
        await session.execute(
            select(PronunciationRuleRow)
            .where(PronunciationRuleRow.project_id == project_id)
            .order_by(PronunciationRuleRow.term)
        )
    ).scalars()
    jobs = (
        await session.execute(
            select(Job)
            .where(Job.project_id == project_id)
            .order_by(Job.status.in_(ACTIVE_JOB_STATUSES).desc(), Job.created_at.desc())
            .limit(10)
        )
    ).scalars()
    return ProjectDetail(
        id=project.id,
        title=project.title,
        author=project.author,
        provider=provider,
        voice_id=project.voice_id,
        model_id=project.model_id,
        voice_settings=VoiceSettings.model_validate(project.voice_settings),
        chapters=[
            ChapterOut(
                id=c.id,
                position=c.position,
                title=c.title,
                segment_count=sum(counts[c.id].values()),
                status_counts=counts[c.id],
                duration_ms=durations.get(c.id, 0),
            )
            for c in chapters
        ],
        rules=[RuleOut.model_validate(r) for r in rules],
        jobs=[JobOut.model_validate(j) for j in jobs],
    )


async def chapter_segments(session: AsyncSession, chapter_id: uuid.UUID) -> list[SegmentOut] | None:
    if await session.get(Chapter, chapter_id) is None:
        return None
    rows = await session.execute(
        select(Segment, AudioBlob.duration_ms)
        .outerjoin(AudioBlob, AudioBlob.key == Segment.cache_key)
        .where(Segment.chapter_id == chapter_id)
        .order_by(Segment.position)
    )
    out: list[SegmentOut] = []
    for segment, duration in rows:
        item = SegmentOut.model_validate(segment)
        item.duration_ms = duration if segment.status == SegmentStatus.CACHED else None
        out.append(item)
    return out


async def segment_out(session: AsyncSession, segment_id: uuid.UUID) -> SegmentOut | None:
    row = (
        await session.execute(
            select(Segment, AudioBlob.duration_ms)
            .outerjoin(AudioBlob, AudioBlob.key == Segment.cache_key)
            .where(Segment.id == segment_id)
        )
    ).first()
    if row is None:
        return None
    segment, duration = row
    item = SegmentOut.model_validate(segment)
    item.duration_ms = duration if segment.status == SegmentStatus.CACHED else None
    return item
