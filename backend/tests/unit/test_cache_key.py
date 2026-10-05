import pytest
from pydantic import ValidationError

from studio.domain.cache_key import synthesis_key
from studio.domain.voice import EffectiveVoice, SegmentOverrides, VoiceSettings, resolve_voice

BASE = resolve_voice("voice-a", "eleven_multilingual_v2", VoiceSettings(), SegmentOverrides())


def test_key_is_stable_and_ignores_whitespace_noise() -> None:
    assert synthesis_key("Hello  world.\n", BASE, "offline") == synthesis_key("Hello world.", BASE, "offline")
    assert len(synthesis_key("x", BASE, "offline")) == 64


def test_unicode_forms_hash_the_same() -> None:
    composed, decomposed = "Café.", "Cafe\u0301."
    assert synthesis_key(composed, BASE, "offline") == synthesis_key(decomposed, BASE, "offline")


FAST = resolve_voice("voice-a", BASE.model_id, VoiceSettings(), SegmentOverrides(speed=1.1))


@pytest.mark.parametrize(
    ("text", "voice", "provider", "take"),
    [
        ("Hello world!", BASE, "offline", 0),
        ("Hello world.", BASE, "elevenlabs", 0),
        ("Hello world.", BASE, "offline", 1),
        ("Hello world.", BASE.model_copy(update={"voice_id": "voice-b"}), "offline", 0),
        ("Hello world.", BASE.model_copy(update={"model_id": "eleven_flash_v2_5"}), "offline", 0),
        ("Hello world.", FAST, "offline", 0),
    ],
)
def test_anything_that_changes_the_audio_changes_the_key(
    text: str, voice: EffectiveVoice, provider: str, take: int
) -> None:
    assert synthesis_key(text, voice, provider, take) != synthesis_key("Hello world.", BASE, "offline")


def test_overrides_replace_only_what_they_set() -> None:
    project = VoiceSettings(stability=0.3, speed=0.9, style=0.2)
    voice = resolve_voice("p-voice", "m", project, SegmentOverrides(voice_id="s-voice", stability=0.8))
    assert voice.voice_id == "s-voice"
    assert voice.settings == VoiceSettings(stability=0.8, speed=0.9, style=0.2)


def test_settings_are_range_checked_like_the_api() -> None:
    with pytest.raises(ValidationError):
        VoiceSettings(speed=2.0)
    with pytest.raises(ValidationError):
        SegmentOverrides(stability=-0.1)
