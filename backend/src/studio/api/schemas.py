from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from studio.db.models import JobKind, JobStatus, SegmentStatus
from studio.domain.voice import VoiceSettings


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RuleIn(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    alias: str = Field(max_length=400)
    case_sensitive: bool = False
    whole_word: bool = True


class RuleOut(RuleIn, ApiModel):
    pass


class RulesPut(BaseModel):
    rules: list[RuleIn] = Field(max_length=2000)


class ChapterOut(ApiModel):
    id: uuid.UUID
    position: int
    title: str
    segment_count: int
    status_counts: dict[str, int]
    duration_ms: int


class JobOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: JobKind
    status: JobStatus
    attempts: int
    max_attempts: int
    progress: dict[str, Any]
    stats: dict[str, Any]
    result: dict[str, Any]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ProjectSummary(ApiModel):
    id: uuid.UUID
    title: str
    author: str | None
    chapter_count: int
    segment_count: int
    created_at: datetime


class ProjectDetail(ApiModel):
    id: uuid.UUID
    title: str
    author: str | None
    provider: str
    voice_id: str
    model_id: str
    voice_settings: VoiceSettings
    chapters: list[ChapterOut]
    rules: list[RuleOut]
    jobs: list[JobOut]


class ProjectPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    voice_id: str | None = Field(None, min_length=1, max_length=100)
    model_id: str | None = Field(None, min_length=1, max_length=100)
    voice_settings: VoiceSettings | None = None


class SegmentOut(ApiModel):
    id: uuid.UUID
    chapter_id: uuid.UUID
    position: int
    paragraph_index: int
    ends_paragraph: bool
    text: str
    voice_id: str | None
    speed: float | None
    stability: float | None
    take: int
    status: SegmentStatus
    error: str | None
    cache_key: str
    duration_ms: int | None = None


class SegmentPatch(BaseModel):
    """Fields that are present are applied; an explicit null clears an override."""

    text: str | None = Field(None, min_length=1)
    voice_id: str | None = None
    speed: float | None = Field(None, ge=0.7, le=1.2)
    stability: float | None = Field(None, ge=0.0, le=1.0)


class SynthesizeRequest(BaseModel):
    chapter_id: uuid.UUID | None = None


class PeaksOut(BaseModel):
    duration_ms: int
    peaks: list[float]


class VoiceOut(BaseModel):
    voice_id: str
    name: str
    description: str


class HealthOut(BaseModel):
    status: str
    provider: str
    queue: dict[str, int]
