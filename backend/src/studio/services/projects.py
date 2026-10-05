"""Project operations that change what a segment should sound like.

Every such change (text edit, voice or settings change, pronunciation rule,
new take) goes through `_refresh_keys`, which recomputes the content address
of the affected segments. A segment whose new key already has audio is
immediately `cached`; otherwise it becomes `pending`. Nothing is synthesized
here; that is the worker's job.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.db.models import Chapter, Project, PronunciationRuleRow, Segment, SegmentStatus
from studio.db.session import SessionFactory
from studio.domain.cache_key import synthesis_key
from studio.domain.manuscript import ParsedBook
from studio.domain.pronunciation import PronunciationDictionary, PronunciationRule
from studio.domain.segmenter import segment_chapter
from studio.domain.voice import EffectiveVoice, SegmentOverrides, VoiceSettings, resolve_voice
from studio.services.audio_cache import AudioCache


class NotFoundError(LookupError):
    pass


class InvalidEditError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceContext:
    voice_id: str
    model_id: str
    settings: VoiceSettings
    dictionary: PronunciationDictionary
    provider: str

    def voice_for(self, segment: Segment) -> EffectiveVoice:
        overrides = SegmentOverrides(
            voice_id=segment.voice_id, speed=segment.speed, stability=segment.stability
        )
        return resolve_voice(self.voice_id, self.model_id, self.settings, overrides)

    def spoken_text(self, text: str) -> str:
        return self.dictionary.apply(text)

    def key_for(self, segment: Segment) -> str:
        return synthesis_key(
            self.spoken_text(segment.text), self.voice_for(segment), self.provider, segment.take
        )


@dataclass(frozen=True, slots=True)
class SegmentChange:
    segment_id: uuid.UUID
    chapter_id: uuid.UUID
    status: SegmentStatus
    cache_key: str

    def event(self) -> dict[str, Any]:
        return {
            "type": "segment",
            "id": str(self.segment_id),
            "chapter_id": str(self.chapter_id),
            "status": self.status.value,
            "cache_key": self.cache_key,
        }


async def load_voice_context(session: AsyncSession, project_id: uuid.UUID, provider: str) -> VoiceContext:
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    rows = await session.execute(
        select(PronunciationRuleRow).where(PronunciationRuleRow.project_id == project_id)
    )
    rules = [PronunciationRule(r.term, r.alias, r.case_sensitive, r.whole_word) for r in rows.scalars()]
    return VoiceContext(
        voice_id=project.voice_id,
        model_id=project.model_id,
        settings=VoiceSettings.model_validate(project.voice_settings),
        dictionary=PronunciationDictionary(rules),
        provider=provider,
    )


class ProjectService:
    def __init__(
        self,
        sessions: SessionFactory,
        cache: AudioCache,
        provider_name: str,
        default_voice_id: str,
        default_model_id: str,
        segment_budget: int = 800,
        segment_hard_limit: int = 4500,
    ) -> None:
        self._sessions = sessions
        self._cache = cache
        self._provider = provider_name
        self._default_voice = default_voice_id
        self._default_model = default_model_id
        self._budget = segment_budget
        self._hard_limit = segment_hard_limit

    async def import_book(self, book: ParsedBook) -> uuid.UUID:
        async with self._sessions() as session, session.begin():
            project = Project(
                title=book.title[:300],
                author=book.author,
                voice_id=self._default_voice,
                model_id=self._default_model,
                voice_settings=VoiceSettings().model_dump(),
            )
            session.add(project)
            await session.flush()
            for position, parsed in enumerate(book.chapters):
                chapter = Chapter(project_id=project.id, position=position, title=parsed.title[:300])
                session.add(chapter)
                await session.flush()
                drafts = segment_chapter(parsed.paragraphs, self._budget, self._hard_limit)
                for i, draft in enumerate(drafts):
                    session.add(
                        Segment(
                            chapter_id=chapter.id,
                            position=i,
                            paragraph_index=draft.paragraph_index,
                            ends_paragraph=draft.ends_paragraph,
                            text=draft.text,
                            take=0,
                            cache_key="",
                        )
                    )
            await session.flush()
            await self._refresh_keys(session, project.id)
            return project.id

    async def update_voice(
        self,
        project_id: uuid.UUID,
        *,
        voice_id: str | None = None,
        model_id: str | None = None,
        settings: VoiceSettings | None = None,
        title: str | None = None,
    ) -> list[SegmentChange]:
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id, with_for_update=True)
            if project is None:
                raise NotFoundError(f"project {project_id} not found")
            if voice_id is not None:
                project.voice_id = voice_id
            if model_id is not None:
                project.model_id = model_id
            if settings is not None:
                project.voice_settings = settings.model_dump()
            if title is not None:
                project.title = title
            await session.flush()
            return await self._refresh_keys(session, project_id)

    async def replace_rules(
        self, project_id: uuid.UUID, rules: Sequence[PronunciationRule]
    ) -> list[SegmentChange]:
        terms = [r.term.strip() for r in rules]
        if len(set(terms)) != len(terms) or any(not t for t in terms):
            raise InvalidEditError("pronunciation terms must be unique and non-empty")
        async with self._sessions() as session, session.begin():
            project = await session.get(Project, project_id, with_for_update=True)
            if project is None:
                raise NotFoundError(f"project {project_id} not found")
            await session.execute(
                delete(PronunciationRuleRow).where(PronunciationRuleRow.project_id == project_id)
            )
            for rule in rules:
                session.add(
                    PronunciationRuleRow(
                        project_id=project_id,
                        term=rule.term.strip(),
                        alias=rule.alias,
                        case_sensitive=rule.case_sensitive,
                        whole_word=rule.whole_word,
                    )
                )
            await session.flush()
            return await self._refresh_keys(session, project_id)

    async def edit_segment(
        self,
        segment_id: uuid.UUID,
        *,
        text: str | None = None,
        overrides: SegmentOverrides | None = None,
        new_take: bool = False,
    ) -> tuple[uuid.UUID, list[SegmentChange]]:
        async with self._sessions() as session, session.begin():
            segment = await session.get(Segment, segment_id, with_for_update=True)
            if segment is None:
                raise NotFoundError(f"segment {segment_id} not found")
            if text is not None:
                cleaned = " ".join(text.split())
                if not cleaned:
                    raise InvalidEditError("segment text cannot be empty")
                if len(cleaned) > self._hard_limit:
                    raise InvalidEditError(f"segment text is limited to {self._hard_limit} characters")
                segment.text = cleaned
            if overrides is not None:
                segment.voice_id = overrides.voice_id
                segment.speed = overrides.speed
                segment.stability = overrides.stability
            if new_take:
                segment.take += 1
            chapter = await session.get_one(Chapter, segment.chapter_id)
            await session.flush()
            changes = await self._refresh_keys(session, chapter.project_id, only=[segment.id])
            return chapter.project_id, changes

    async def _refresh_keys(
        self, session: AsyncSession, project_id: uuid.UUID, only: list[uuid.UUID] | None = None
    ) -> list[SegmentChange]:
        ctx = await load_voice_context(session, project_id, self._provider)
        query = select(Segment).join(Chapter).where(Chapter.project_id == project_id)
        if only is not None:
            query = query.where(Segment.id.in_(only))
        segments = list((await session.execute(query)).scalars())
        new_keys = {s.id: ctx.key_for(s) for s in segments}
        cached = await self._cache.get_many(new_keys.values())
        changes: list[SegmentChange] = []
        for segment in segments:
            key = new_keys[segment.id]
            status = SegmentStatus.CACHED if key in cached else SegmentStatus.PENDING
            is_cached = status == SegmentStatus.CACHED
            # Same address and the same answer to "is there audio?": keep the current
            # status, including queued/generating/failed and its error message.
            if key == segment.cache_key and is_cached == (segment.status == SegmentStatus.CACHED):
                continue
            segment.cache_key = key
            segment.status = status
            segment.error = None
            changes.append(SegmentChange(segment.id, segment.chapter_id, status, key))
        await session.flush()
        return changes
