"""Turns chapter paragraphs into synthesis segments.

Rules, in order of priority:

1. A segment never spans two paragraphs. Paragraph breaks are where the
   renderer inserts longer pauses, and keeping them aligned means editing a
   paragraph only ever touches that paragraph's segments.
2. A segment never ends mid-sentence. Sentences are packed greedily up to
   `budget` characters.
3. A single sentence longer than `budget` becomes its own segment. Only a
   sentence longer than `hard_limit` (the provider's request limit) is cut, at
   clause punctuation first and whitespace as a last resort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from studio.domain.sentences import split_sentences

_CLAUSE_BREAK = re.compile(r"(?<=[;:,—–])\s+")


@dataclass(frozen=True, slots=True)
class SegmentDraft:
    text: str
    paragraph_index: int
    ends_paragraph: bool


def _cut(text: str, limit: int, pattern: re.Pattern[str]) -> list[str]:
    parts = pattern.split(text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}".strip() if current else part
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def _split_oversized(sentence: str, hard_limit: int) -> list[str]:
    if len(sentence) <= hard_limit:
        return [sentence]
    out: list[str] = []
    for clause_chunk in _cut(sentence, hard_limit, _CLAUSE_BREAK):
        if len(clause_chunk) <= hard_limit:
            out.append(clause_chunk)
        else:
            out.extend(_cut(clause_chunk, hard_limit, re.compile(r"\s+")))
    return out


def segment_paragraph(paragraph: str, budget: int, hard_limit: int) -> list[str]:
    pieces: list[str] = []
    for sentence in split_sentences(paragraph):
        pieces.extend(_split_oversized(sentence, hard_limit))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= budget:
            current = f"{current} {piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def segment_chapter(paragraphs: list[str], budget: int = 800, hard_limit: int = 4500) -> list[SegmentDraft]:
    if budget <= 0 or hard_limit < budget:
        raise ValueError("budget must be positive and not larger than hard_limit")
    drafts: list[SegmentDraft] = []
    paragraph_index = 0
    for paragraph in paragraphs:
        chunks = segment_paragraph(paragraph, budget, hard_limit)
        if not chunks:
            continue
        for i, chunk in enumerate(chunks):
            drafts.append(SegmentDraft(chunk, paragraph_index, ends_paragraph=i == len(chunks) - 1))
        paragraph_index += 1
    return drafts
