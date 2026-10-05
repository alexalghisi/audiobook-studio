"""ElevenLabs text-to-speech adapter.

Speaks the public HTTP API directly (POST /v1/text-to-speech/{voice_id}) so
the retry behaviour is ours and testable:

- 429 is retried after the server's Retry-After (seconds or an HTTP date),
  falling back to exponential backoff when the header is missing. ElevenLabs
  answers 429 both for rate limits and for too many concurrent requests.
- 5xx and transport errors are retried with capped exponential backoff and
  full jitter.
- Other 4xx are the caller's fault and fail immediately.

When retries run out the error is still marked retryable, so the job layer can
try the segment again later instead of failing the whole book.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from studio.tts.base import ProviderError, SynthesisRequest, SynthesisResult, Voice

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 6
    base_delay: float = 0.5
    max_delay: float = 20.0
    max_retry_after: float = 60.0

    def backoff(self, attempt: int, rand: Callable[[], float]) -> float:
        return rand() * min(self.max_delay, self.base_delay * 2.0**attempt)


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def _error_message(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return f"{detail.get('status', 'error')}: {detail.get('message', '')}".strip()
    if isinstance(detail, list) and detail:
        return "; ".join(
            str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail
        )
    return str(body)[:200]


class ElevenLabsProvider:
    name = "elevenlabs"
    default_voice_id = DEFAULT_VOICE_ID
    default_model_id = DEFAULT_MODEL_ID

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        output_format: str = "mp3_44100_128",
        concurrency: int = 4,
        retry: RetryPolicy | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        if not api_key:
            raise ValueError("an ElevenLabs API key is required")
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._owns_client = client is None
        self._headers = {"xi-api-key": api_key}
        self._output_format = output_format
        self._concurrency = concurrency
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._rand = rand

    @property
    def max_concurrency(self) -> int:
        return self._concurrency

    def _body(self, request: SynthesisRequest) -> dict[str, Any]:
        settings = request.voice.settings
        body: dict[str, Any] = {
            "text": request.text,
            "model_id": request.voice.model_id,
            "voice_settings": {
                "stability": settings.stability,
                "similarity_boost": settings.similarity_boost,
                "style": settings.style,
                "use_speaker_boost": settings.use_speaker_boost,
                "speed": settings.speed,
            },
        }
        if request.previous_text:
            body["previous_text"] = request.previous_text
        if request.next_text:
            body["next_text"] = request.next_text
        return body

    async def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        response = await self._send(
            "POST",
            f"/v1/text-to-speech/{request.voice.voice_id}",
            params={"output_format": self._output_format},
            headers={**self._headers, "Accept": "audio/mpeg"},
            json=self._body(request),
        )
        if not response.content:
            raise ProviderError("empty audio response", retryable=True, status=response.status_code)
        return SynthesisResult(
            audio=response.content, format="mp3", request_id=response.headers.get("request-id")
        )

    async def list_voices(self) -> list[Voice]:
        response = await self._send("GET", "/v1/voices", headers=self._headers)
        voices: list[dict[str, Any]] = response.json().get("voices", [])
        return [Voice(v["voice_id"], v.get("name", v["voice_id"]), v.get("category") or "") for v in voices]

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error = "no attempt made"
        for attempt in range(self._retry.max_attempts):
            delay: float | None
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_error = f"transport error: {exc.__class__.__name__}"
                delay = self._retry.backoff(attempt, self._rand)
            else:
                status = response.status_code
                if status < 400:
                    return response
                last_error = f"HTTP {status} {_error_message(response)}"
                if status == 429:
                    delay = parse_retry_after(response.headers.get("retry-after"))
                    if delay is None:
                        delay = self._retry.backoff(attempt, self._rand)
                    delay = min(delay, self._retry.max_retry_after)
                elif status >= 500:
                    delay = self._retry.backoff(attempt, self._rand)
                else:
                    raise ProviderError(last_error, retryable=False, status=status)
            if attempt + 1 < self._retry.max_attempts:
                await self._sleep(delay)
        raise ProviderError(
            f"gave up after {self._retry.max_attempts} attempts: {last_error}", retryable=True
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
