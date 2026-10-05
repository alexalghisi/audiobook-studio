from __future__ import annotations

import numpy as np
import numpy.typing as npt

PEAK_RATE = 8_000  # decode rate used for waveform analysis


def compute_peaks(pcm: npt.NDArray[np.int16], buckets: int) -> list[float]:
    """Max absolute amplitude per bucket, scaled to 0..1, for drawing a waveform."""
    if buckets <= 0 or pcm.size == 0:
        return []
    buckets = min(buckets, int(pcm.size))
    edges = np.linspace(0, pcm.size, buckets + 1).astype(np.int64)
    magnitude = np.abs(pcm.astype(np.int32))
    peaks = np.maximum.reduceat(magnitude, edges[:-1]) / 32768.0
    return [round(float(p), 3) for p in peaks]


def buckets_for(duration_ms: int, per_second: int, minimum: int = 32, maximum: int = 4000) -> int:
    return max(minimum, min(maximum, duration_ms * per_second // 1000))
