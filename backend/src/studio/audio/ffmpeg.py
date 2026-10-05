"""Thin async wrappers around the ffmpeg and ffprobe binaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 44_100


class FfmpegError(RuntimeError):
    pass


async def run(args: list[str], stdin: bytes | None = None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await proc.communicate(stdin)
    except asyncio.CancelledError:
        # Don't leave an ffmpeg process running after a cancelled job.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip().splitlines()[-5:]
        raise FfmpegError(f"{Path(args[0]).name} exited with {proc.returncode}: {' | '.join(tail)}")
    return out


async def ffmpeg(*args: str, stdin: bytes | None = None) -> bytes:
    return await run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args], stdin)


async def ffmpeg_stderr(*args: str) -> str:
    """Runs ffmpeg for its analysis output (loudnorm, ebur128), which goes to stderr."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg exited with {proc.returncode}: {err.decode(errors='replace')[-400:]}")
    return err.decode(errors="replace")


async def probe(path: Path) -> dict[str, Any]:
    out = await run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-show_chapters",
         str(path)]
    )  # fmt: skip
    result: dict[str, Any] = json.loads(out)
    return result


async def decode_pcm(source: Path | bytes, sample_rate: int = SAMPLE_RATE) -> npt.NDArray[np.int16]:
    """Decodes any audio ffmpeg understands to mono signed 16-bit PCM."""
    if isinstance(source, Path):
        raw = await ffmpeg("-i", str(source), "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-")
    else:
        raw = await ffmpeg(
            "-i", "pipe:0", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-", stdin=source
        )
    return np.frombuffer(raw, dtype="<i2").astype(np.int16)


async def encode_mp3(pcm: npt.NDArray[np.int16], sample_rate: int, bitrate: str = "128k") -> bytes:
    return await ffmpeg(
        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
        "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", "-",
        stdin=pcm.astype("<i2").tobytes(),
    )  # fmt: skip
