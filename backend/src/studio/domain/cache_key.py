"""Content address for a synthesized segment.

The key covers everything that changes the audio a provider returns: the text
*after* pronunciation rules (so adding a rule only invalidates segments that
contain the term), the voice, every setting, the provider and its model, and
a take number that is bumped when the user explicitly asks for a new take.

Neighbouring text sent for request stitching (previous_text/next_text) is
deliberately left out. Including it would mean editing one paragraph changes
the keys of the segments on either side, so a one-word fix would cost three
synthesis requests instead of one.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

from studio.domain.voice import EffectiveVoice

KEY_VERSION = 1


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def synthesis_key(text: str, voice: EffectiveVoice, provider: str, take: int = 0) -> str:
    settings = voice.settings.model_dump()
    payload = {
        "v": KEY_VERSION,
        "provider": provider,
        "model": voice.model_id,
        "voice": voice.voice_id,
        "settings": {k: round(v, 3) if isinstance(v, float) else v for k, v in sorted(settings.items())},
        "take": take,
        "text": normalize_text(text),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
