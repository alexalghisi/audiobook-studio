from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[float]: JSONB}


def _enum(cls: type[StrEnum]) -> Enum:
    return Enum(cls, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e])


class SegmentStatus(StrEnum):
    PENDING = "pending"  # no audio for the current text and settings
    QUEUED = "queued"
    GENERATING = "generating"
    CACHED = "cached"
    FAILED = "failed"


class JobKind(StrEnum):
    SYNTHESIZE = "synthesize"
    EXPORT = "export"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Project(Timestamped, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str | None] = mapped_column(String(300))
    voice_id: Mapped[str] = mapped_column(String(100))
    model_id: Mapped[str] = mapped_column(String(100))
    voice_settings: Mapped[dict[str, Any]]

    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="project", order_by="Chapter.position", cascade="all, delete-orphan"
    )
    rules: Mapped[list[PronunciationRuleRow]] = relationship(cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("project_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))

    project: Mapped[Project] = relationship(back_populates="chapters")
    segments: Mapped[list[Segment]] = relationship(
        back_populates="chapter", order_by="Segment.position", cascade="all, delete-orphan"
    )


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("chapter_id", "position"),
        Index("ix_segments_cache_key", "cache_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    paragraph_index: Mapped[int] = mapped_column(Integer)
    ends_paragraph: Mapped[bool] = mapped_column(Boolean)
    text: Mapped[str] = mapped_column(Text)
    voice_id: Mapped[str | None] = mapped_column(String(100))
    speed: Mapped[float | None] = mapped_column(Float)
    stability: Mapped[float | None] = mapped_column(Float)
    take: Mapped[int] = mapped_column(Integer, default=0)
    cache_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[SegmentStatus] = mapped_column(_enum(SegmentStatus), default=SegmentStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chapter: Mapped[Chapter] = relationship(back_populates="segments")


class PronunciationRuleRow(Base):
    __tablename__ = "pronunciation_rules"
    __table_args__ = (UniqueConstraint("project_id", "term"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    term: Mapped[str] = mapped_column(String(200))
    alias: Mapped[str] = mapped_column(String(400))
    case_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    whole_word: Mapped[bool] = mapped_column(Boolean, default=True)


class AudioBlob(Base):
    """Index of the content-addressed audio store. The bytes live on disk."""

    __tablename__ = "audio_blobs"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    format: Mapped[str] = mapped_column(String(8))
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    peaks: Mapped[list[float]]
    provider: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Timestamped, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # At most one active job per dedupe key: double-clicking "Export" returns the running job.
        Index(
            "uq_jobs_active_dedupe",
            "project_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[JobKind] = mapped_column(_enum(JobKind))
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.QUEUED)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
