import pytest

from studio.domain.sentences import split_sentences


def test_splits_simple_sentences() -> None:
    assert split_sentences("It rained. We stayed in! Did you?") == ["It rained.", "We stayed in!", "Did you?"]


@pytest.mark.parametrize(
    "text",
    [
        "Mr. Darcy bowed and Mrs. Bennet sighed.",
        "Dr. Watson met J. R. R. Tolkien at St. Paul's.",
        "We flew to the U.S. after lunch.",
        "Bring tools, e.g. a hammer, and wait.",
        "The value was 3.14 exactly.",
    ],
)
def test_abbreviations_initials_and_numbers_do_not_split(text: str) -> None:
    assert split_sentences(text) == [text]


def test_dialogue_followed_by_attribution_stays_together() -> None:
    text = '"Stop!" she said. "Why?" he asked.'
    assert split_sentences(text) == ['"Stop!" she said.', '"Why?" he asked.']


def test_curly_quotes_with_inner_sentences_are_kept_whole() -> None:
    text = "“I went home. Then I slept,” he said. It was late."
    assert split_sentences(text) == ["“I went home. Then I slept,” he said.", "It was late."]


def test_quote_ending_a_sentence_is_a_boundary() -> None:
    text = "She whispered, “Run.” Nobody moved."
    assert split_sentences(text) == ["She whispered, “Run.”", "Nobody moved."]


def test_ellipsis_only_splits_before_a_capital() -> None:
    assert split_sentences("Well... maybe not. Wait... What?") == ["Well... maybe not.", "Wait...", "What?"]


def test_text_without_terminator_is_one_sentence() -> None:
    assert split_sentences("  a fragment with no end  ") == ["a fragment with no end"]
    assert split_sentences("   ") == []
