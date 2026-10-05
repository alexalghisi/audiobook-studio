"""Sentence boundary detection tuned for narrative prose.

A regex alone splits on every period. Fiction is full of cases where that is
wrong: honorifics ("Mr. Darcy"), initials ("J. R. R. Tolkien"), dialogue whose
punctuation sits inside quotes ('"Stop!" she said.') and sentences quoted
across several clauses. The splitter finds candidate terminators and then
rejects the ones that are not real boundaries.
"""

from __future__ import annotations

import re

# Abbreviations that are almost never the end of a sentence.
_NON_TERMINAL_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "mx",
        "dr",
        "prof",
        "st",
        "jr",
        "sr",
        "vs",
        "fig",
        "no",
        "nos",
        "mt",
        "capt",
        "gen",
        "col",
        "lt",
        "sgt",
        "rev",
        "hon",
        "gov",
        "sen",
        "rep",
        "pres",
        "e.g",
        "i.e",
        "cf",
        "approx",
        "dept",
        "est",
        "vol",
        "ch",
        "pp",
        "ave",
        "blvd",
        "rd",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    }
)

_TERMINATOR = re.compile(r"(?:\.\.\.|…|[.!?])+[\"”’)\]]*(?=\s)")
_OPENING = '"“‘([—'
_DOTTED_ACRONYM = re.compile(r"(?:\b[A-Za-z]\.){2,}$")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")


def _quote_depth(text: str) -> list[int]:
    """Depth of double-quote nesting *before* each character index.

    Curly quotes nest. Straight quotes toggle, which is the best that can be
    done without a parser.
    """
    depth = 0
    straight_open = False
    out: list[int] = []
    for ch in text:
        out.append(depth + (1 if straight_open else 0))
        if ch == "“":
            depth += 1
        elif ch == "”":
            depth = max(0, depth - 1)
        elif ch == '"':
            straight_open = not straight_open
    return out


def _starts_new_sentence(text: str, pos: int) -> bool:
    """Whether the text after the whitespace at `pos` looks like a sentence start."""
    i = pos
    while i < len(text) and text[i].isspace():
        i += 1
    while i < len(text) and text[i] in _OPENING:
        i += 1
    if i >= len(text):
        return True
    ch = text[i]
    return ch.isupper() or ch.isdigit()


def _is_abbreviation(before: str) -> bool:
    m = _WORD_BEFORE.search(before)
    if m is None:
        return False
    word = m.group(1)
    if _DOTTED_ACRONYM.search(before):
        return True
    if len(word) == 1 and word.isupper():
        return True  # an initial: "J. Smith"
    return word.lower().rstrip(".") in _NON_TERMINAL_ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences, keeping punctuation and quotes attached."""
    text = text.strip()
    if not text:
        return []
    depth = _quote_depth(text)
    sentences: list[str] = []
    start = 0
    for m in _TERMINATOR.finditer(text):
        end = m.end()
        token = m.group(0)
        # Terminator inside an unclosed quotation: the quote continues.
        if depth[end] > 0:
            continue
        if not _starts_new_sentence(text, end):
            continue
        if (
            token.startswith(".")
            and not token.startswith("...")
            and _is_abbreviation(text[start : m.start()])
        ):
            continue
        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        start = end
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences
