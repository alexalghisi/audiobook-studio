"""Loudness normalization and measurement (EBU R128 via ffmpeg)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from studio.audio.ffmpeg import ffmpeg_stderr


@dataclass(frozen=True, slots=True)
class LoudnessTarget:
    """Defaults sit inside ACX's requirements (RMS -23..-18 dB, peaks below -3 dBFS)."""

    integrated: float = -18.0
    true_peak: float = -3.0
    lra: float = 11.0

    def filter(self) -> str:
        return f"loudnorm=I={self.integrated}:TP={self.true_peak}:LRA={self.lra}"


@dataclass(frozen=True, slots=True)
class Loudness:
    integrated: float
    true_peak: float
    lra: float


_JSON_BLOCK = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


async def analyze(path: Path, target: LoudnessTarget) -> dict[str, str]:
    """First loudnorm pass: measures the input so the second pass can be linear."""
    stderr = await ffmpeg_stderr(
        "-i", str(path), "-af", f"{target.filter()}:print_format=json", "-f", "null", "-"
    )
    match = _JSON_BLOCK.search(stderr)
    if match is None:
        raise RuntimeError("loudnorm produced no measurement")
    measured: dict[str, str] = json.loads(match.group(0))
    return measured


def second_pass_filter(target: LoudnessTarget, measured: dict[str, str]) -> str:
    # With the measurements, loudnorm applies a single linear gain when that is enough
    # to reach the target without breaking the peak limit, so dynamics are untouched.
    # Otherwise it falls back to its dynamic mode. The true-peak limit holds either way.
    return (
        f"{target.filter()}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true:print_format=json"
    )


_SUMMARY = re.compile(
    r"Integrated loudness:\s+I:\s+(?P<i>-?[\d.]+|-inf) LUFS.*?"
    r"Loudness range:\s+LRA:\s+(?P<lra>-?[\d.]+) LU.*?"
    r"True peak:\s+Peak:\s+(?P<tp>-?[\d.]+|-inf) dBFS",
    re.S,
)


async def measure(path: Path) -> Loudness:
    """Independent EBU R128 measurement of a finished file (ffmpeg's ebur128 filter)."""
    stderr = await ffmpeg_stderr(
        "-nostats", "-i", str(path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    )  # fmt: skip
    summary = stderr[stderr.rfind("Summary:") :]
    match = _SUMMARY.search(summary)
    if match is None:
        raise RuntimeError("could not parse ebur128 summary")

    def num(value: str) -> float:
        return float("-inf") if value == "-inf" else float(value)

    return Loudness(integrated=num(match["i"]), true_peak=num(match["tp"]), lra=num(match["lra"]))
