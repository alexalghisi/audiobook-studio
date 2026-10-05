"""Manuscript import: Markdown, plain text and EPUB into chapters of paragraphs."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree


class ManuscriptError(ValueError):
    pass


@dataclass(slots=True)
class ParsedChapter:
    title: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedBook:
    title: str
    author: str | None
    chapters: list[ParsedChapter]


_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


# --- Markdown ---------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(?=\S)(.+?)(?<=\S)\1")


def _strip_inline_markdown(text: str) -> str:
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    previous = None
    while previous != text:
        previous = text
        text = _EMPHASIS.sub(r"\2", text)
    return _clean(text)


def _markdown_blocks(source: str) -> list[tuple[int, str]]:
    """(heading level or 0 for a paragraph, text) in document order."""
    blocks: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_code = False

    def flush() -> None:
        if buffer:
            text = _strip_inline_markdown(" ".join(buffer))
            if text:
                blocks.append((0, text))
            buffer.clear()

    for raw in source.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            flush()
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            blocks.append((len(heading.group(1)), _strip_inline_markdown(heading.group(2))))
        elif not line.strip() or _RULE.match(line):
            flush()
        else:
            line = line.lstrip()
            while line.startswith(">"):
                line = line[1:].lstrip()
            buffer.append(_LIST_MARKER.sub("", line))
    flush()
    return blocks


def parse_markdown(source: str, fallback_title: str) -> ParsedBook:
    blocks = _markdown_blocks(source)
    h1 = sum(1 for level, _ in blocks if level == 1)
    h2 = sum(1 for level, _ in blocks if level == 2)
    title = fallback_title
    if h1 == 1 and h2 >= 1:
        chapter_level = 2
        title = next(text for level, text in blocks if level == 1)
        blocks = [b for b in blocks if b[0] != 1]
    elif h1 >= 1:
        chapter_level = 1
    elif h2 >= 1:
        chapter_level = 2
    else:
        chapter_level = 0

    chapters: list[ParsedChapter] = []
    for level, text in blocks:
        if chapter_level and level == chapter_level:
            chapters.append(ParsedChapter(text))
            continue
        if not chapters:
            chapters.append(ParsedChapter("Opening" if chapter_level else title))
        # Sub-headings are read aloud as their own short paragraph.
        chapters[-1].paragraphs.append(text)
    return _finish(title, None, chapters)


# --- Plain text -------------------------------------------------------------

_TEXT_CHAPTER = re.compile(
    r"^\s*(?:(?:chapter|part|book)\s+(?:\d+|[ivxlcdm]+|[a-z-]+)\b.*|prologue|epilogue)\s*$",
    re.IGNORECASE,
)


def parse_text(source: str, fallback_title: str) -> ParsedBook:
    paragraphs = [_clean(p) for p in re.split(r"\n\s*\n", source.replace("\r\n", "\n"))]
    chapters: list[ParsedChapter] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        if _TEXT_CHAPTER.match(paragraph) and len(paragraph) <= 80:
            chapters.append(ParsedChapter(paragraph))
        else:
            if not chapters:
                chapters.append(ParsedChapter(fallback_title))
            chapters[-1].paragraphs.append(paragraph)
    return _finish(fallback_title, None, chapters)


# --- EPUB -------------------------------------------------------------------

_BLOCK_TAGS = frozenset({"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "section", "br"})
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "nav"})


class _XhtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._buffer: list[str] = []
        self._tag_stack: list[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = _clean("".join(self._buffer))
        self._buffer.clear()
        if text:
            kind = next((t for t in reversed(self._tag_stack) if t in _BLOCK_TAGS), "p")
            self.blocks.append(("heading" if kind.startswith("h") and len(kind) == 2 else "p", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._flush()
        if tag != "br":
            self._tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._flush()
        if tag in self._tag_stack:
            while self._tag_stack and self._tag_stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_epub(data: bytes, fallback_title: str) -> ParsedBook:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(el for el in container.iter() if _local(el.tag) == "rootfile")
        opf_path = rootfile.attrib["full-path"]
        opf = ElementTree.fromstring(archive.read(opf_path))
    except (zipfile.BadZipFile, KeyError, StopIteration, ElementTree.ParseError) as exc:
        raise ManuscriptError(f"not a readable EPUB: {exc}") from exc

    title = fallback_title
    author: str | None = None
    manifest: dict[str, str] = {}
    spine: list[str] = []
    for el in opf.iter():
        name = _local(el.tag)
        if name == "title" and el.text:
            title = _clean(el.text)
        elif name == "creator" and el.text and author is None:
            author = _clean(el.text)
        elif name == "item" and "html" in el.attrib.get("media-type", ""):
            manifest[el.attrib["id"]] = el.attrib["href"]
        elif name == "itemref":
            spine.append(el.attrib["idref"])

    base = posixpath.dirname(opf_path)
    chapters: list[ParsedChapter] = []
    for idref in spine:
        href = manifest.get(idref)
        if href is None:
            continue
        parser = _XhtmlText()
        parser.feed(archive.read(posixpath.join(base, href)).decode("utf-8", errors="replace"))
        parser.close()
        chapter: ParsedChapter | None = None
        for kind, text in parser.blocks:
            if chapter is None:
                chapter = ParsedChapter(text if kind == "heading" else f"Chapter {len(chapters) + 1}")
                if kind == "heading":
                    continue
            chapter.paragraphs.append(text)
        if chapter is not None and chapter.paragraphs:
            chapters.append(chapter)
    return _finish(title, author, chapters)


# --- Entry point ------------------------------------------------------------


def _finish(title: str, author: str | None, chapters: list[ParsedChapter]) -> ParsedBook:
    chapters = [c for c in chapters if c.paragraphs]
    if not chapters:
        raise ManuscriptError("the manuscript contains no text")
    return ParsedBook(title=title, author=author, chapters=chapters)


def parse_manuscript(filename: str, data: bytes) -> ParsedBook:
    stem, _, ext = filename.rpartition(".")
    fallback = (stem or filename).replace("_", " ").replace("-", " ").strip() or "Untitled"
    ext = ext.lower()
    if ext == "epub":
        return parse_epub(data, fallback)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManuscriptError("text manuscripts must be UTF-8") from exc
    if ext in {"md", "markdown"}:
        return parse_markdown(text, fallback)
    if ext in {"txt", "text"}:
        return parse_text(text, fallback)
    raise ManuscriptError(f"unsupported file type: .{ext}")
