import io
import zipfile

import pytest

from studio.domain.manuscript import ManuscriptError, parse_manuscript

MARKDOWN = """# The Lighthouse

## Chapter One

The keeper climbed the *stairs*, as he did every [night](https://example.com).
He counted them.

> “Ninety-nine,” he said.

### A smaller heading

- a list item

```
code is skipped
```

## Chapter Two

---

The lamp went out.
"""


def test_markdown_single_h1_is_the_title_and_h2s_are_chapters() -> None:
    book = parse_manuscript("lighthouse.md", MARKDOWN.encode())
    assert book.title == "The Lighthouse"
    assert [c.title for c in book.chapters] == ["Chapter One", "Chapter Two"]
    assert book.chapters[0].paragraphs == [
        "The keeper climbed the stairs, as he did every night. He counted them.",
        "“Ninety-nine,” he said.",
        "A smaller heading",
        "a list item",
    ]
    assert book.chapters[1].paragraphs == ["The lamp went out."]


def test_markdown_without_headings_is_one_chapter() -> None:
    book = parse_manuscript("short-story.md", b"First.\n\nSecond.")
    assert book.title == "short story"
    assert [(c.title, c.paragraphs) for c in book.chapters] == [("short story", ["First.", "Second."])]


def test_plain_text_detects_chapters_and_unwraps_lines() -> None:
    text = "Prologue\n\nIt began\nquietly.\n\nCHAPTER I\n\nThen it\r\ndid not.\n\nChapter 2: The End\n\nDone."
    book = parse_manuscript("book.txt", text.encode())
    assert [(c.title, c.paragraphs) for c in book.chapters] == [
        ("Prologue", ["It began quietly."]),
        ("CHAPTER I", ["Then it did not."]),
        ("Chapter 2: The End", ["Done."]),
    ]


def _epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Night Train</dc:title>'
            "<dc:creator>A. Writer</dc:creator></metadata><manifest>"
            '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="css" href="style.css" media-type="text/css"/></manifest>'
            '<spine><itemref idref="cover"/><itemref idref="c1"/></spine></package>',
        )
        z.writestr("OEBPS/cover.xhtml", "<html><body><div><img src='c.jpg'/></div></body></html>")
        z.writestr(
            "OEBPS/text/c1.xhtml",
            "<html><head><title>x</title><style>p{}</style></head><body>"
            "<h1>Departure</h1><p>The train left at <em>nine</em>.</p>"
            "<p>Nobody&#8217;s ticket was checked.</p><script>bad()</script></body></html>",
        )
    return buf.getvalue()


def test_epub_follows_the_spine_and_skips_empty_documents() -> None:
    book = parse_manuscript("night.epub", _epub())
    assert (book.title, book.author) == ("Night Train", "A. Writer")
    assert [(c.title, c.paragraphs) for c in book.chapters] == [
        ("Departure", ["The train left at nine.", "Nobody’s ticket was checked."])
    ]


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("x.pdf", b"%PDF", "unsupported"),
        ("x.md", b"   \n\n", "no text"),
        ("x.epub", b"not a zip", "EPUB"),
        ("x.txt", b"\xff\xfe\xfa", "UTF-8"),
    ],
)
def test_rejects_bad_input(name: str, data: bytes, message: str) -> None:
    with pytest.raises(ManuscriptError, match=message):
        parse_manuscript(name, data)
