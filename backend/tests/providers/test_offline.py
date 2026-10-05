import shutil

import pytest

from studio.audio.wav import wav_duration_ms
from studio.domain.voice import SegmentOverrides, VoiceSettings, resolve_voice
from studio.tts.base import ProviderError, SynthesisRequest
from studio.tts.offline import EspeakProvider, FormantProvider

SHORT = "The lamp went out."
LONG = "The lamp went out, and for a long while nobody in the house said a single word about it."


providers = [pytest.param(FormantProvider(), id="formant")]
if shutil.which("espeak-ng"):
    providers.append(pytest.param(EspeakProvider(), id="espeak"))


@pytest.mark.parametrize("provider", providers)
async def test_offline_audio_is_deterministic_valid_wav_and_scales_with_text(
    provider: FormantProvider | EspeakProvider,
) -> None:
    v = resolve_voice("narrator-us-m", "m", VoiceSettings(), SegmentOverrides())
    a = await provider.synthesize(SynthesisRequest(SHORT, v))
    b = await provider.synthesize(SynthesisRequest(SHORT, v))
    long = await provider.synthesize(SynthesisRequest(LONG, v))

    assert a.format == "wav"
    assert a.audio == b.audio
    short_ms, long_ms = wav_duration_ms(a.audio), wav_duration_ms(long.audio)
    assert short_ms > 500
    assert long_ms > short_ms * 3


@pytest.mark.parametrize("provider", providers)
async def test_speed_setting_changes_duration(provider: FormantProvider | EspeakProvider) -> None:
    slow = resolve_voice("narrator-us-m", "m", VoiceSettings(speed=0.7), SegmentOverrides())
    fast = resolve_voice("narrator-us-m", "m", VoiceSettings(speed=1.2), SegmentOverrides())
    slow_ms = wav_duration_ms((await provider.synthesize(SynthesisRequest(LONG, slow))).audio)
    fast_ms = wav_duration_ms((await provider.synthesize(SynthesisRequest(LONG, fast))).audio)
    assert slow_ms > fast_ms * 1.3


@pytest.mark.parametrize("provider", providers)
async def test_unknown_voice_is_a_permanent_error(provider: FormantProvider | EspeakProvider) -> None:
    v = resolve_voice("nobody", "m", VoiceSettings(), SegmentOverrides())
    with pytest.raises(ProviderError) as info:
        await provider.synthesize(SynthesisRequest(SHORT, v))
    assert not info.value.retryable
