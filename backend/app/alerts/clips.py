"""Clip validation: the clip must belong to THIS event and camera, live under the clips root, and be sendable."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Optional

from .schemas import ClipInfo


class ClipRejected(Exception):
    """Permanent problem: this metadata/file can never be attached to the incident."""


class ClipNotReady(Exception):
    """Transient: file missing or still being written. Retry later."""


class ClipValidator:
    def __init__(self, root: str | os.PathLike, *, max_bytes: int = 16 * 1024 * 1024,
                 min_bytes: int = 1024, min_age_s: float = 1.0):
        self.root = Path(root).resolve()
        self.max_bytes, self.min_bytes = max_bytes, min_bytes
        self.min_age_s = min_age_s

    # -------------------------------------------------------------- resolve
    def _resolve(self, rel: str, camera_id: Optional[str] = None) -> Path:
        p = PurePosixPath(rel.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise ClipRejected("clip_path must be relative and must not contain '..'")
        if len(p.parts) > 1 and camera_id and p.parts[0].lower() != camera_id.lower():
            raise ClipRejected("clip is stored under a different camera folder")
        if p.suffix.lower() != ".mp4":
            raise ClipRejected("only .mp4 clips are accepted")
        cand = self.root.joinpath(*p.parts)
        if os.path.realpath(cand) != os.path.normpath(cand):
            raise ClipRejected("symlinks are not allowed in clip paths")
        if not Path(os.path.realpath(cand)).is_relative_to(self.root):
            raise ClipRejected("clip path escapes the clips root")
        return cand

    # --------------------------------------------------- send-time re-check
    def load_verified_bytes(self, clip: ClipInfo, camera_id: Optional[str] = None) -> bytes:
        """Re-read the file at SEND time and confirm it is byte-identical to what we validated."""
        path = self._resolve(clip.clip_path, camera_id)
        data = path.read_bytes()
        if len(data) != clip.size_bytes or hashlib.sha256(data).hexdigest() != clip.sha256:
            raise ClipRejected("clip changed on disk after validation")
        return data
