"""Offline providers so the whole studio runs without network access or keys.

`EspeakProvider` speaks real (robotic) English through espeak-ng. Where the
binary isn't installed, `FormantProvider` produces deterministic speech-like
audio instead. Both are deterministic: the same request gives the same bytes,
which the cache tests rely on. espeak-ng has no equivalent of stability or
style, so those settings only affect the cache key here.
"""

from __future__ import annotations

import asyncio
import os
import shutil

from studio.audio.wav import pcm_to_wav, repair_streamed_wav
from studio.tts import formant
from studio.tts.base import ProviderError, SynthesisRequest, SynthesisResult, Voice

# voice_id -> (espeak-ng voice, base pitch 0-99, description)
OFFLINE_VOICES: dict[str, tuple[str, int, str]] = {
    "narrator-us-m": ("en-us+m3", 45, "US English, male narrator"),
    "narrator-us-f": ("en-us+f3", 55, "US English, female narrator"),
    "narrator-gb-m": ("en-gb-x-rp+m1", 40, "British English (RP), male"),
    "narrator-gb-f": ("en-gb+f2", 60, "British English, female"),
}
DEFAULT_VOICE = "narrator-us-m"
_ESPEAK_WPM = 165


def _voice_entry(voice_id: str) -> tuple[str, int, str]:
    try:
        return OFFLINE_VOICES[voice_id]
    except KeyError:
        raise ProviderError(f"unknown offline voice: {voice_id}", retryable=False, status=404) from None


class EspeakProvider:
    def __init__(self, binary: str = "espeak-ng", concurrency: int | None = None) -> None:
        self._binary = binary
        self._concurrency = concurrency or max(1, os.cpu_count() or 1)

    name = "offline-espeak"
    default_voice_id = DEFAULT_VOICE
    default_model_id = "espeak-ng"

    @property
    def max_concurrency(self) -> int:
        return self._concurrency

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        espeak_voice, pitch, _ = _voice_entry(request.voice.voice_id)
        settings = request.voice.settings
        proc = await asyncio.create_subprocess_exec(
            self._binary, "--stdin", "--stdout",
            "-v", espeak_voice,
            "-s", str(round(_ESPEAK_WPM * settings.speed)),
            "-p", str(pitch),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, err = await proc.communicate(request.text.encode())
        if proc.returncode != 0 or not out:
            raise ProviderError(f"espeak-ng failed: {err.decode(errors='replace')[:200]}", retryable=True)
        return SynthesisResult(audio=repair_streamed_wav(out), format="wav")

    async def list_voices(self) -> list[Voice]:
        return [Voice(voice_id, voice_id, desc) for voice_id, (_, _, desc) in OFFLINE_VOICES.items()]

    async def aclose(self) -> None:
        return None


class FormantProvider:
    name = "offline-formant"
    default_voice_id = DEFAULT_VOICE
    default_model_id = "formant-v1"
    max_concurrency = 2

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        _, pitch, _ = _voice_entry(request.voice.voice_id)
        pcm = await asyncio.to_thread(
            formant.synthesize_pcm,
            request.text,
            request.voice.voice_id,
            request.voice.settings.speed,
            80.0 + pitch * 1.6,
        )
        return SynthesisResult(audio=pcm_to_wav(pcm, formant.SAMPLE_RATE), format="wav")

    async def list_voices(self) -> list[Voice]:
        return [Voice(voice_id, voice_id, desc) for voice_id, (_, _, desc) in OFFLINE_VOICES.items()]

    async def aclose(self) -> None:
        return None


def offline_provider() -> EspeakProvider | FormantProvider:
    binary = shutil.which("espeak-ng")
    return EspeakProvider(binary) if binary else FormantProvider()
