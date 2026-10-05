"""A tiny deterministic formant synthesizer.

It does not produce words. It produces speech-*like* audio: voiced vowels
with two formants over a glottal pulse train, noise bursts for consonants and
real pauses at spaces and punctuation, with duration proportional to the text.
That is enough to exercise the whole pipeline (stitching, loudness
normalization, waveforms, chapter markers) where espeak-ng is not installed,
and it is what the fake ElevenLabs server returns.
"""

from __future__ import annotations

import hashlib

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 22_050

# (F1, F2) in Hz, roughly the textbook values for English vowels.
_VOWEL_FORMANTS: dict[str, tuple[float, float]] = {
    "a": (730, 1090),
    "e": (530, 1840),
    "i": (270, 2290),
    "o": (570, 840),
    "u": (300, 870),
    "y": (270, 2290),
}
_PAUSES_MS = {",": 180, ";": 220, ":": 220, ".": 380, "!": 380, "?": 380, "—": 220, "\n": 400}


def _seed(text: str, voice: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{voice}\0{text}".encode()).digest()[:8], "little")


def synthesize_pcm(
    text: str, voice: str = "default", speed: float = 1.0, pitch_hz: float = 120.0
) -> npt.NDArray[np.int16]:
    rng = np.random.default_rng(_seed(text, voice))
    char_s = 0.065 / speed
    parts: list[npt.NDArray[np.float64]] = []
    phase = 0.0
    for index, ch in enumerate(text):
        lower = ch.lower()
        if lower in _PAUSES_MS:
            parts.append(np.zeros(int(SAMPLE_RATE * _PAUSES_MS[lower] / 1000 / speed)))
            continue
        if ch.isspace():
            parts.append(np.zeros(int(SAMPLE_RATE * char_s * 0.5)))
            continue
        n = int(SAMPLE_RATE * char_s)
        t = np.arange(n) / SAMPLE_RATE
        envelope = np.sin(np.pi * np.arange(n) / n) ** 0.5
        if lower in _VOWEL_FORMANTS:
            f1, f2 = _VOWEL_FORMANTS[lower]
            # Gentle intonation: pitch drifts over the sentence.
            f0 = pitch_hz * (1.0 + 0.08 * np.sin(index / 7.0))
            glottal = np.sign(np.sin(2 * np.pi * f0 * t + phase)) * 0.5 + 0.5
            phase += 2 * np.pi * f0 * n / SAMPLE_RATE
            sound = glottal * (0.6 * np.sin(2 * np.pi * f1 * t) + 0.4 * np.sin(2 * np.pi * f2 * t))
            parts.append(sound * envelope)
        elif ch.isalnum():
            noise = rng.standard_normal(n // 2)
            parts.append(np.concatenate([noise * 0.25 * envelope[: n // 2], np.zeros(n - n // 2)]))
    if not parts:
        parts.append(np.zeros(int(SAMPLE_RATE * 0.2)))
    signal = np.concatenate(parts)
    peak = float(np.max(np.abs(signal))) or 1.0
    return (signal / peak * 0.7 * 32767).astype(np.int16)
