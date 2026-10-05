from __future__ import annotations

import io
import struct
import wave

import numpy as np
import numpy.typing as npt


def pcm_to_wav(pcm: npt.NDArray[np.int16], sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.astype("<i2").tobytes())
    return buf.getvalue()


def repair_streamed_wav(data: bytes) -> bytes:
    """Fixes the RIFF/data sizes of a WAV written to a pipe.

    Tools that stream WAV to stdout (espeak-ng among them) can't seek back to
    fill in the sizes, so the header says 0 or 0x7fffffff. Some decoders
    tolerate that and some don't.
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV stream")
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if chunk_id == b"data":
            actual = len(data) - pos - 8
            fixed = bytearray(data)
            struct.pack_into("<I", fixed, 4, len(data) - 8)
            struct.pack_into("<I", fixed, pos + 4, actual)
            return bytes(fixed)
        pos += 8 + size + (size & 1)
    raise ValueError("WAV stream has no data chunk")


def wav_duration_ms(data: bytes) -> int:
    with wave.open(io.BytesIO(data), "rb") as w:
        return round(w.getnframes() * 1000 / w.getframerate())
