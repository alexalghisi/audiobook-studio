"""A local stand-in for the ElevenLabs TTS API, with fault injection.

It validates requests the way the real API does (API key, voice id, setting
ranges, text length), answers with real MP3 audio and can be told to fail:
scripted responses (`fail_next`), a random failure rate, latency, and a
concurrency limit that answers 429 like ElevenLabs does when an account runs
too many requests at once.

    python -m studio.testing.fake_elevenlabs --port 8790 --failure-rate 0.1
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from studio.audio.ffmpeg import encode_mp3
from studio.tts import formant

SUPPORTED_FORMATS = {"mp3_44100_128", "mp3_44100_64", "mp3_22050_32"}
MAX_TEXT = 10_000
_SETTING_RANGES = {
    "stability": (0.0, 1.0),
    "similarity_boost": (0.0, 1.0),
    "style": (0.0, 1.0),
    "speed": (0.7, 1.2),
}


@dataclass(slots=True)
class Fault:
    status: int
    retry_after: str | None = None
    message: str = "injected fault"


@dataclass(slots=True)
class RecordedRequest:
    voice_id: str
    body: dict[str, Any]
    output_format: str | None
    status: int


@dataclass
class FakeElevenLabs:
    api_key: str = "test-key"
    voices: dict[str, str] = field(
        default_factory=lambda: {"JBFqnCBsd6RMkjVDRZzb": "George", "EXAVITQu4vr4xnSDxMaL": "Sarah"}
    )
    failure_rate: float = 0.0
    latency_s: float = 0.0
    concurrency_limit: int | None = None
    seed: int = 7
    requests: list[RecordedRequest] = field(default_factory=list)
    _faults: deque[Fault] = field(default_factory=deque)
    _active: int = 0
    _audio_cache: dict[tuple[str, str, float], bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def fail_next(self, status: int, *, times: int = 1, retry_after: str | None = None) -> None:
        for _ in range(times):
            self._faults.append(Fault(status, retry_after))

    @property
    def synthesized_texts(self) -> list[str]:
        return [str(r.body.get("text")) for r in self.requests if r.status == 200]

    @staticmethod
    def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"detail": {"status": code, "message": message}}, status, headers=headers)

    async def _tts(self, request: Request) -> Response:
        voice_id = request.path_params["voice_id"]
        output_format = request.query_params.get("output_format")
        try:
            body: dict[str, Any] = await request.json()
        except ValueError:
            body = {}
        response = await self._handle_tts(request, voice_id, output_format, body)
        self.requests.append(RecordedRequest(voice_id, body, output_format, response.status_code))
        return response

    async def _handle_tts(
        self, request: Request, voice_id: str, output_format: str | None, body: dict[str, Any]
    ) -> Response:
        if request.headers.get("xi-api-key") != self.api_key:
            return self._error(401, "invalid_api_key", "Invalid API key")
        if self.concurrency_limit is not None and self._active >= self.concurrency_limit:
            return self._error(429, "too_many_concurrent_requests", "Too many concurrent requests")
        if self._faults:
            fault = self._faults.popleft()
            headers = {"retry-after": fault.retry_after} if fault.retry_after else None
            return self._error(fault.status, "injected", fault.message, headers)
        if self.failure_rate and self._rng.random() < self.failure_rate:
            return self._error(503, "service_unavailable", "Injected random failure")
        if voice_id not in self.voices:
            return self._error(404, "voice_not_found", f"A voice with the voice_id {voice_id} was not found.")
        if output_format is not None and output_format not in SUPPORTED_FORMATS:
            return self._error(422, "invalid_output_format", f"Unsupported output format {output_format}")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return JSONResponse({"detail": [{"loc": ["body", "text"], "msg": "invalid text"}]}, 422)
        settings = body.get("voice_settings") or {}
        for key, (low, high) in _SETTING_RANGES.items():
            value = settings.get(key)
            if value is not None and not (isinstance(value, (int, float)) and low <= value <= high):
                return JSONResponse(
                    {
                        "detail": [
                            {"loc": ["body", "voice_settings", key], "msg": f"must be in [{low}, {high}]"}
                        ]
                    },
                    422,
                )
        self._active += 1
        try:
            if self.latency_s:
                await asyncio.sleep(self.latency_s)
            audio = await self._audio(text, voice_id, float(settings.get("speed", 1.0)))
        finally:
            self._active -= 1
        return Response(
            audio,
            media_type="audio/mpeg",
            headers={"request-id": uuid.uuid4().hex, "x-character-count": str(len(text))},
        )

    async def _audio(self, text: str, voice_id: str, speed: float) -> bytes:
        key = (text, voice_id, speed)
        if key not in self._audio_cache:
            pcm = await asyncio.to_thread(formant.synthesize_pcm, text, voice_id, speed)
            self._audio_cache[key] = await encode_mp3(pcm, formant.SAMPLE_RATE, "64k")
        return self._audio_cache[key]

    async def _voices(self, request: Request) -> Response:
        if request.headers.get("xi-api-key") != self.api_key:
            return self._error(401, "invalid_api_key", "Invalid API key")
        return JSONResponse(
            {
                "voices": [
                    {"voice_id": vid, "name": name, "category": "premade"}
                    for vid, name in self.voices.items()
                ]
            }
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/v1/text-to-speech/{voice_id}", self._tts, methods=["POST"]),
                Route("/v1/voices", self._voices, methods=["GET"]),
            ]
        )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--api-key", default="test-key")
    parser.add_argument("--failure-rate", type=float, default=0.0)
    parser.add_argument("--latency", type=float, default=0.0)
    parser.add_argument("--concurrency-limit", type=int, default=None)
    args = parser.parse_args()
    fake = FakeElevenLabs(
        api_key=args.api_key,
        failure_rate=args.failure_rate,
        latency_s=args.latency,
        concurrency_limit=args.concurrency_limit,
    )
    uvicorn.run(fake.app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
