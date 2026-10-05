"""Pronunciation dictionary: alias replacement applied before synthesis.

All rules are compiled into one alternation and applied in a single pass, so
an alias is never itself rewritten by another rule, and when two terms
overlap the longest one wins ("New York City" before "New York").
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PronunciationRule:
    term: str
    alias: str
    case_sensitive: bool = False
    whole_word: bool = True


class PronunciationDictionary:
    def __init__(self, rules: list[PronunciationRule]) -> None:
        usable = [r for r in rules if r.term.strip()]
        # Longest first: Python's alternation takes the first alternative that matches.
        self._rules = sorted(usable, key=lambda r: len(r.term), reverse=True)
        self._pattern = self._compile(self._rules) if self._rules else None

    @staticmethod
    def _compile(rules: list[PronunciationRule]) -> re.Pattern[str]:
        alternatives = []
        for i, rule in enumerate(rules):
            body = re.escape(rule.term)
            if rule.whole_word:
                # \b fails for terms that start or end with punctuation ("C++"), lookarounds don't.
                body = rf"(?<!\w){body}(?!\w)"
            if not rule.case_sensitive:
                body = f"(?i:{body})"
            alternatives.append(f"(?P<r{i}>{body})")
        return re.compile("|".join(alternatives))

    def apply(self, text: str) -> str:
        if self._pattern is None:
            return text

        def replace(match: re.Match[str]) -> str:
            assert match.lastgroup is not None
            return self._rules[int(match.lastgroup[1:])].alias

        return self._pattern.sub(replace, text)

    def affects(self, text: str) -> bool:
        return self._pattern is not None and self._pattern.search(text) is not None
