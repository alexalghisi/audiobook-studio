"""Deterministic synthetic manuscripts for tests and benchmarks."""

from __future__ import annotations

import random

_SUBJECTS = [
    "The keeper",
    "Mara",
    "The old dog",
    "Her brother",
    "Mr. Hale",
    "The ferryman",
    "Nobody",
    "The child",
]
_VERBS = ["watched", "followed", "remembered", "ignored", "carried", "counted", "painted", "forgot"]
_OBJECTS = [
    "the lamp at the top of the stairs",
    "every boat that came in after dark",
    "the letters from the mainland",
    "a map with half the islands missing",
    "the bell that nobody rang anymore",
    "the salt on the window glass",
]
_TAILS = ["before the tide turned", "without saying a word", "until the fog lifted", "as if it mattered", ""]
_DIALOGUE = ["“Not tonight,” she said.", "“Did you hear that?” he asked.", "“Leave it,” said Mr. Hale."]


def sentence(rng: random.Random, n: int) -> str:
    tail = rng.choice(_TAILS)
    body = f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} {rng.choice(_OBJECTS)}"
    # The counter keeps every sentence unique, so tests can map text back to a segment.
    return f"{body} {tail} on day {n}.".replace("  ", " ")


def make_markdown(
    chapters: int, paragraphs: int, sentences: int, seed: int = 1, title: str = "Salt and Light"
) -> str:
    rng = random.Random(seed)
    counter = 0
    out = [f"# {title}", ""]
    for c in range(1, chapters + 1):
        out += [f"## Chapter {c}", ""]
        for p in range(paragraphs):
            parts = []
            for _ in range(sentences):
                counter += 1
                parts.append(sentence(rng, counter))
            if p % 3 == 2:
                parts.append(rng.choice(_DIALOGUE))
            out += [" ".join(parts), ""]
    return "\n".join(out)
