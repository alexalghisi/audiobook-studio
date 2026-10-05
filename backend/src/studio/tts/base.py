"""The port every speech provider implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from studio.domain.voice import EffectiveVoice

AudioFormat = Literal["mp3", "wav"]


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    text: str
    voice: EffectiveVoice
    # Neighbouring text lets the provider keep prosody continuous across
    # segment boundaries. It is a hint and not part of the cache key.
    previous_text: str | None = None
    next_text: str | None = None


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    audio: bytes
    format: AudioFormat
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class Voice:
    voice_id: str
    name: str
    description: str = ""


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class TTSProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable identifier; part of every cache key."""
        ...

    @property
    def default_voice_id(self) -> str: ...

    @property
    def default_model_id(self) -> str: ...

    @property
    def max_concurrency(self) -> int: ...

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult: ...

    async def list_voices(self) -> list[Voice]: ...

    async def aclose(self) -> None: ...
