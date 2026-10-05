from studio.domain.pronunciation import PronunciationDictionary, PronunciationRule


def apply(rules: list[PronunciationRule], text: str) -> str:
    return PronunciationDictionary(rules).apply(text)


def test_replaces_whole_words_case_insensitively_by_default() -> None:
    rules = [PronunciationRule("Hermione", "her-MY-oh-nee")]
    assert apply(rules, "hermione and HERMIONE, not Hermiones.") == (
        "her-MY-oh-nee and her-MY-oh-nee, not Hermiones."
    )


def test_case_sensitive_rule_only_matches_exact_case() -> None:
    rules = [PronunciationRule("US", "United States", case_sensitive=True)]
    assert apply(rules, "The US told us.") == "The United States told us."


def test_longest_term_wins_when_terms_overlap() -> None:
    rules = [PronunciationRule("New York", "Noo York"), PronunciationRule("New York City", "N Y C")]
    assert apply(rules, "New York City is in New York.") == "N Y C is in Noo York."


def test_aliases_are_not_rewritten_by_other_rules() -> None:
    rules = [PronunciationRule("GIF", "jif"), PronunciationRule("jif", "peanut butter")]
    assert apply(rules, "A GIF.") == "A jif."


def test_terms_with_punctuation_and_unicode() -> None:
    rules = [PronunciationRule("C++", "C plus plus"), PronunciationRule("Zoë", "Zo-ee")]
    assert apply(rules, "Zoë writes C++, not C+.") == "Zo-ee writes C plus plus, not C+."


def test_substring_rule_when_whole_word_is_off() -> None:
    rules = [PronunciationRule("ough", "off", whole_word=False)]
    assert apply(rules, "Cough") == "Coff"


def test_empty_dictionary_and_blank_terms_are_no_ops() -> None:
    assert apply([], "unchanged") == "unchanged"
    assert apply([PronunciationRule("  ", "x")], "unchanged") == "unchanged"


def test_affects_reports_whether_a_text_would_change() -> None:
    d = PronunciationDictionary([PronunciationRule("Nguyen", "win")])
    assert d.affects("Mr. Nguyen")
    assert not d.affects("Mr. Smith")
