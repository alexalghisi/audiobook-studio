import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from studio.audio.ffmpeg import probe
from studio.domain.voice import SegmentOverrides, VoiceSettings, resolve_voice
from studio.testing.fake_elevenlabs import FakeElevenLabs
from studio.testing.server import free_port, serve
from studio.tts.base import ProviderError, SynthesisRequest
from studio.tts.elevenlabs import ElevenLabsProvider, RetryPolicy, parse_retry_after

VOICE = resolve_voice(
    "JBFqnCBsd6RMkjVDRZzb",
    "eleven_multilingual_v2",
    VoiceSettings(stability=0.4, speed=1.1),
    SegmentOverrides(),
)


@pytest.fixture
def fake() -> FakeElevenLabs:
    return FakeElevenLabs(api_key="secret")


@pytest.fixture
def base_url(fake: FakeElevenLabs) -> Iterator[str]:
    with serve(fake.app()) as url:
        yield url


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
async def provider(base_url: str, sleeps: Sleeps) -> AsyncIterator[ElevenLabsProvider]:
    p = ElevenLabsProvider(
        "secret",
        base_url=base_url,
        sleep=sleeps,
        rand=lambda: 1.0,
        retry=RetryPolicy(max_attempts=4, base_delay=0.5),
    )
    yield p
    await p.aclose()


async def test_sends_the_documented_request_and_returns_playable_mp3(
    provider: ElevenLabsProvider, fake: FakeElevenLabs, tmp_path: Path
) -> None:
    result = await provider.synthesize(
        SynthesisRequest(
            "The keeper climbed the stairs.", VOICE, previous_text="It was dark.", next_text="He waited."
        )
    )

    assert result.format == "mp3"
    assert result.request_id
    [recorded] = fake.requests
    assert recorded.voice_id == "JBFqnCBsd6RMkjVDRZzb"
    assert recorded.output_format == "mp3_44100_128"
    assert recorded.body == {
        "text": "The keeper climbed the stairs.",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.1,
        },
        "previous_text": "It was dark.",
        "next_text": "He waited.",
    }
    out = tmp_path / "a.mp3"
    out.write_bytes(result.audio)
    info = await probe(out)
    assert info["streams"][0]["codec_name"] == "mp3"
    assert float(info["format"]["duration"]) > 0.5


async def test_429_waits_for_retry_after_seconds(
    provider: ElevenLabsProvider, fake: FakeElevenLabs, sleeps: Sleeps
) -> None:
    fake.fail_next(429, retry_after="3")
    await provider.synthesize(SynthesisRequest("Hello.", VOICE))
    assert sleeps.calls == [3.0]
    assert [r.status for r in fake.requests] == [429, 200]


def test_retry_after_accepts_http_dates_and_rejects_garbage() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_retry_after(format_datetime(now + timedelta(seconds=7), usegmt=True), now) == 7.0
    assert parse_retry_after(format_datetime(now - timedelta(seconds=7), usegmt=True), now) == 0.0
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after(None, now) is None


async def test_retry_after_is_capped(base_url: str, fake: FakeElevenLabs, sleeps: Sleeps) -> None:
    fake.fail_next(429, retry_after="3600")
    p = ElevenLabsProvider("secret", base_url=base_url, sleep=sleeps, retry=RetryPolicy(max_retry_after=30))
    await p.synthesize(SynthesisRequest("Hello.", VOICE))
    await p.aclose()
    assert sleeps.calls == [30.0]


async def test_5xx_is_retried_with_exponential_backoff(
    provider: ElevenLabsProvider, fake: FakeElevenLabs, sleeps: Sleeps
) -> None:
    fake.fail_next(503, times=2)
    fake.fail_next(500)
    await provider.synthesize(SynthesisRequest("Hello.", VOICE))
    assert sleeps.calls == [0.5, 1.0, 2.0]
    assert [r.status for r in fake.requests] == [503, 503, 500, 200]


async def test_gives_up_with_a_retryable_error(provider: ElevenLabsProvider, fake: FakeElevenLabs) -> None:
    fake.fail_next(502, times=10)
    with pytest.raises(ProviderError, match="gave up after 4 attempts: HTTP 502") as info:
        await provider.synthesize(SynthesisRequest("Hello.", VOICE))
    assert info.value.retryable
    assert len(fake.requests) == 4


async def test_concurrency_limit_429_without_header_backs_off(base_url: str, fake: FakeElevenLabs) -> None:
    fake.concurrency_limit = 1
    fake.latency_s = 0.2
    waited: list[float] = []

    async def real_sleep(seconds: float) -> None:
        waited.append(seconds)
        await asyncio.sleep(seconds)

    p = ElevenLabsProvider("secret", base_url=base_url, sleep=real_sleep, retry=RetryPolicy(base_delay=0.2))
    await asyncio.gather(*(p.synthesize(SynthesisRequest(f"Line {i}.", VOICE)) for i in range(3)))
    await p.aclose()
    statuses = [r.status for r in fake.requests]
    assert statuses.count(200) == 3
    assert 429 in statuses
    assert waited


@pytest.mark.parametrize(
    ("api_key", "voice_id", "text", "status", "message"),
    [
        ("wrong", "JBFqnCBsd6RMkjVDRZzb", "Hi.", 401, "invalid_api_key"),
        ("secret", "no-such-voice", "Hi.", 404, "voice_not_found"),
        ("secret", "JBFqnCBsd6RMkjVDRZzb", "x" * 10_001, 422, "invalid text"),
    ],
)
async def test_client_errors_fail_fast_without_retrying(
    base_url: str,
    fake: FakeElevenLabs,
    sleeps: Sleeps,
    api_key: str,
    voice_id: str,
    text: str,
    status: int,
    message: str,
) -> None:
    p = ElevenLabsProvider(api_key, base_url=base_url, sleep=sleeps)
    voice = VOICE.model_copy(update={"voice_id": voice_id})
    with pytest.raises(ProviderError, match=message) as info:
        await p.synthesize(SynthesisRequest(text, voice))
    await p.aclose()
    assert (info.value.retryable, info.value.status) == (False, status)
    assert len(fake.requests) == 1
    assert sleeps.calls == []


async def test_transport_errors_are_retried(sleeps: Sleeps) -> None:
    p = ElevenLabsProvider(
        "secret",
        base_url=f"http://127.0.0.1:{free_port()}",
        sleep=sleeps,
        retry=RetryPolicy(max_attempts=3),
        rand=lambda: 1.0,
    )
    with pytest.raises(ProviderError, match="transport error") as info:
        await p.synthesize(SynthesisRequest("Hello.", VOICE))
    await p.aclose()
    assert info.value.retryable
    assert sleeps.calls == [0.5, 1.0]


async def test_lists_voices(provider: ElevenLabsProvider) -> None:
    voices = await provider.list_voices()
    assert {v.voice_id for v in voices} == {"JBFqnCBsd6RMkjVDRZzb", "EXAVITQu4vr4xnSDxMaL"}


def test_requires_an_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        ElevenLabsProvider("", client=httpx.AsyncClient())
