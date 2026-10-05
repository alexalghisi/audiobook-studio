import pytest

from studio.domain.segmenter import segment_chapter, segment_paragraph
from studio.domain.sentences import split_sentences


def test_packs_whole_sentences_up_to_the_budget() -> None:
    paragraph = "One two three. Four five six. Seven eight nine. Ten."
    chunks = segment_paragraph(paragraph, budget=30, hard_limit=100)
    assert chunks == ["One two three. Four five six.", "Seven eight nine. Ten."]
    assert all(len(c) <= 30 for c in chunks)


def test_never_splits_inside_a_sentence() -> None:
    paragraph = (
        "Mr. Hale opened the door. “It is late,” he said. “Come in. Sit down.” "
        "The fire was low... Nobody spoke for a while."
    )
    chunks = segment_paragraph(paragraph, budget=40, hard_limit=500)
    sentences = split_sentences(paragraph)
    rejoined = " ".join(chunks)
    assert rejoined == " ".join(sentences)
    for chunk in chunks:
        # every chunk is a concatenation of whole sentences
        assert any(chunk.startswith(s) for s in sentences)
        assert any(chunk.endswith(s) for s in sentences)


def test_long_sentence_is_kept_whole_below_the_hard_limit() -> None:
    sentence = "This sentence, which goes on, and on, and on, is long. "
    chunks = segment_paragraph(sentence + "Short one.", budget=20, hard_limit=200)
    assert chunks == [sentence.strip(), "Short one."]


def test_sentence_above_the_hard_limit_is_cut_at_clauses_first() -> None:
    sentence = "alpha beta gamma, delta epsilon zeta; eta theta iota kappa."
    chunks = segment_paragraph(sentence, budget=20, hard_limit=25)
    assert chunks == ["alpha beta gamma,", "delta epsilon zeta;", "eta theta iota kappa."]


def test_segments_never_span_paragraphs_and_mark_paragraph_ends() -> None:
    drafts = segment_chapter(["Go. Run. Hide.", "", "Wait."], budget=5, hard_limit=10)
    assert [(d.text, d.paragraph_index, d.ends_paragraph) for d in drafts] == [
        ("Go.", 0, False),
        ("Run.", 0, False),
        ("Hide.", 0, True),
        ("Wait.", 1, True),
    ]


def test_rejects_inconsistent_limits() -> None:
    with pytest.raises(ValueError, match="budget"):
        segment_chapter(["x"], budget=100, hard_limit=10)
