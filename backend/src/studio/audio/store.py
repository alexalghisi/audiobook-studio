"""Content-addressed file store.

Files are written to a temporary name and renamed into place, so a reader
never sees a partial file and a crash mid-write leaves only a stray temp file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class ContentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str, ext: str) -> Path:
        if len(key) < 8 or not key.isalnum():
            raise ValueError(f"invalid content key: {key!r}")
        return self.root / key[:2] / key[2:4] / f"{key}.{ext}"

    def exists(self, key: str, ext: str) -> bool:
        return self.path(key, ext).is_file()

    def write(self, key: str, ext: str, data: bytes) -> Path:
        target = self.path(key, ext)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    def adopt(self, key: str, ext: str, source: Path) -> Path:
        """Moves an already written file (e.g. ffmpeg output) into the store."""
        target = self.path(key, ext)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        return target
