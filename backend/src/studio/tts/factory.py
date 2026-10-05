from __future__ import annotations

from studio.config import Settings
from studio.tts.base import TTSProvider
from studio.tts.elevenlabs import ElevenLabsProvider
from studio.tts.offline import offline_provider


def build_provider(settings: Settings) -> TTSProvider:
    """ElevenLabs when a key is configured, otherwise the offline provider."""
    if settings.elevenlabs_api_key:
        return ElevenLabsProvider(
            settings.elevenlabs_api_key,
            base_url=settings.elevenlabs_base_url,
            concurrency=settings.elevenlabs_concurrency,
        )
    return offline_provider()
