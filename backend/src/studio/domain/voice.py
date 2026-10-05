"""Voice settings: project defaults plus per-segment overrides."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VoiceSettings(BaseModel):
    """Mirrors ElevenLabs' `voice_settings` object; the ranges are the API's."""

    model_config = ConfigDict(frozen=True)

    stability: float = Field(0.5, ge=0.0, le=1.0)
    similarity_boost: float = Field(0.75, ge=0.0, le=1.0)
    style: float = Field(0.0, ge=0.0, le=1.0)
    speed: float = Field(1.0, ge=0.7, le=1.2)
    use_speaker_boost: bool = True


class SegmentOverrides(BaseModel):
    model_config = ConfigDict(frozen=True)

    voice_id: str | None = None
    speed: float | None = Field(None, ge=0.7, le=1.2)
    stability: float | None = Field(None, ge=0.0, le=1.0)


class EffectiveVoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    voice_id: str
    model_id: str
    settings: VoiceSettings


def resolve_voice(
    voice_id: str, model_id: str, settings: VoiceSettings, overrides: SegmentOverrides
) -> EffectiveVoice:
    patch: dict[str, float] = {}
    if overrides.speed is not None:
        patch["speed"] = overrides.speed
    if overrides.stability is not None:
        patch["stability"] = overrides.stability
    return EffectiveVoice(
        voice_id=overrides.voice_id or voice_id,
        model_id=model_id,
        settings=settings.model_copy(update=patch),
    )
