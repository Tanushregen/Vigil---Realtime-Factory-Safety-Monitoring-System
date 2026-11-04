"""Find and validate the MP4 that belongs to one alert event.

Supports both:
  1. Explicit clip paths in the JSON (`clip_path` or aliases like `video_path`, `video`, `clip`, etc.).
  2. Automatic discovery inside `clips_root` using `clip_id`, `event_id`, `camera_id`, or the JSON file's own
     stem when no explicit clip path is present in the JSON, while rejecting ambiguous matches so the wrong
     video is never attached.

Exceptions (same classes as the rest of the package):
    ClipNotReady  - transient: file missing or still being written  -> retry later
    ClipRejected  - this file can never be sent as-is (wrong camera/event, ambiguous, not an MP4, too big, wrong codec)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

from .clips import ClipNotReady, ClipRejected, ClipValidator
from .schemas import AlertEvent, ClipInfo

log = logging.getLogger("vigil.clips")

__all__ = ["AlertClipValidator", "ClipNotReady", "ClipRejected", "clip_name_matches_event"]


def _norm_id(val: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", val.lower())


def clip_name_matches_event(stem: str, event_id: str) -> bool:
    """event001 <-> EVT001 : file name equals the event id, normalized alphanumeric stem matches,
    or both end in the same number.

    This prevents accidentally attaching event001.mp4 to EVT002 when clip paths are misconfigured.
    """
    s, e = stem.strip().lower(), event_id.strip().lower()
    if not s or not e:
        return False
    if s == e or _norm_id(s) == _norm_id(e):
        return True
    ms, me = re.search(r"(\d+)$", s), re.search(r"(\d+)$", e)
    return bool(ms and me and int(ms.group(1)) == int(me.group(1)))


class AlertClipValidator(ClipValidator):
    def __init__(self, clips_root: str | os.PathLike = "clips", base_dir: str | os.PathLike = ".", *,
                 enforce_name_match: bool = True, check_codecs: bool = True, **kw):
        super().__init__(clips_root, **kw)
        self.base_dir = Path(base_dir).resolve()
        self.enforce_name_match = enforce_name_match
        self.check_codecs = check_codecs
        self._ffprobe = shutil.which("ffprobe")
        if check_codecs and not self._ffprobe:
            log.warning("ffprobe not found: H.264/AAC codec check is skipped (only the MP4 header is verified). "
                        "WhatsApp rejects other codecs at upload time.")

    # ------------------------------------------------------------ locate
    def _locate(self, clip_path: Optional[str], camera_id: Optional[str],
                *, ev: Optional[AlertEvent] = None) -> Tuple[Path, str]:
        """Resolve a JSON's video clip to `(absolute_path, path_relative_to_clips_root)`."""
        if clip_path:
            p = PurePosixPath(clip_path.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or not p.parts:
                raise ClipRejected("clip_path must be relative and must not contain '..'")
            candidates: List[Path] = [self.base_dir.joinpath(*p.parts), self.root.joinpath(*p.parts)]
            if camera_id and p.parts[0].lower() != camera_id.lower() and p.parts[0].lower() != self.root.name.lower():
                candidates.append(self.root.joinpath(camera_id, *p.parts))
            if ev and ev.source_path:
                src_dir = Path(ev.source_path).resolve().parent
                candidates.append(src_dir.joinpath(*p.parts))
            inside = [c for c in candidates if Path(os.path.normpath(c)).is_relative_to(self.root)]
            if not inside:
                raise ClipRejected(f"clip_path is outside the clips folder ({self.root.name}/)")
            chosen = next((c for c in inside if c.exists()), inside[0])
            rel = Path(os.path.normpath(chosen)).relative_to(self.root).as_posix()
            self._resolve(rel, camera_id)   # camera folder == camera_id (when present), .mp4 only, no symlinks
            return chosen, rel

        # Automatic discovery when clip_path is omitted from the JSON
        if ev is None:
            raise ClipRejected("missing clip_path and event metadata for video discovery")
        return self._discover_clip(ev)

    def _discover_clip(self, ev: AlertEvent) -> Tuple[Path, str]:
        """Find the unique .mp4 clip in `self.root` matching the event's identifiers or JSON filename."""
        target_ids: List[str] = []
        if ev.clip_id:
            target_ids.append(ev.clip_id)
        if ev.event_id and not ev.event_id.startswith("EVT_"):
            target_ids.append(ev.event_id)
        if ev.source_path:
            src_stem = Path(ev.source_path).stem
            if src_stem and src_stem not in target_ids:
                target_ids.append(src_stem)

        if not target_ids:
            raise ClipRejected("JSON does not contain clip_path or an identifiable event_id/clip_id")

        if not self.root.exists():
            raise ClipNotReady(f"clips directory not found: {self.root}")

        # Gather candidate search directories inside self.root
        search_dirs: List[Path] = []
        if ev.camera_id:
            cam_dir = self.root / ev.camera_id
            if cam_dir.is_dir():
                search_dirs.append(cam_dir)
            else:
                # Case-insensitive camera folder lookup
                for child in sorted(self.root.iterdir()):
                    if child.is_dir() and child.name.lower() == ev.camera_id.lower():
                        search_dirs.append(child)
            search_dirs.append(self.root)
        else:
            search_dirs.append(self.root)
            for child in sorted(self.root.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    search_dirs.append(child)

        matches: List[Path] = []
        seen: set[Path] = set()
        for d in search_dirs:
            if not d.is_dir():
                continue
            for mp4 in sorted(d.glob("*.mp4")):
                if mp4 in seen or mp4.is_symlink():
                    continue
                seen.add(mp4)
                rel_parts = mp4.relative_to(self.root).parts
                if len(rel_parts) > 1 and ev.camera_id and rel_parts[0].lower() != ev.camera_id.lower():
                    continue
                if any(clip_name_matches_event(mp4.stem, tid) for tid in target_ids):
                    matches.append(mp4)

        if not matches:
            raise ClipNotReady(f"no matching clip found for event {ev.event_id}")
        if len(matches) > 1:
            rels = ", ".join(m.relative_to(self.root).as_posix() for m in matches)
            raise ClipRejected(f"ambiguous clip match for event {ev.event_id}: {rels}")

        chosen = matches[0]
        rel = Path(os.path.normpath(chosen)).relative_to(self.root).as_posix()
        self._resolve(rel, ev.camera_id)
        return chosen, rel

    # ---------------------------------------------------------- validate
    def validate_event_clip(self, ev: AlertEvent) -> ClipInfo:
        path, rel = self._locate(ev.clip_path, ev.camera_id, ev=ev)
        if self.enforce_name_match:
            valid_ids = [x for x in (ev.clip_id, ev.event_id) if x and not x.startswith("EVT_")]
            if ev.source_path:
                src_stem = Path(ev.source_path).stem
                if src_stem and not valid_ids:
                    valid_ids.append(src_stem)
            if valid_ids and not any(clip_name_matches_event(path.stem, vid) for vid in valid_ids):
                raise ClipRejected(f"clip file name '{path.name}' does not match event {ev.event_id}")
        if not path.is_file():
            raise ClipNotReady(f"clip file not found: {ev.clip_path or rel}")
        st = path.stat()
        if time.time() - st.st_mtime < self.min_age_s:
            raise ClipNotReady("clip file was modified moments ago (still being written?)")
        if st.st_size < self.min_bytes:
            raise ClipNotReady("clip file is too small to be a valid MP4 (empty or still being written?)")
        if st.st_size > self.max_bytes:
            raise ClipRejected(f"clip is {st.st_size / 1048576:.1f} MB; WhatsApp allows at most "
                               f"{self.max_bytes // 1048576} MB for video")

        sha = hashlib.sha256()
        with open(path, "rb") as f:
            head = f.read(12)
            sha.update(head)
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        if head[4:8] != b"ftyp":
            raise ClipRejected("file is not a valid MP4 container (missing 'ftyp' header)")

        duration = self._check_streams(path) if (self.check_codecs and self._ffprobe) else None
        return ClipInfo(clip_id=path.stem, clip_path=rel, size_bytes=st.st_size, sha256=sha.hexdigest(),
                        duration_seconds=duration)

    def _check_streams(self, path: Path) -> float | None:
        """ffprobe: must decode as MP4 with an H.264 video stream and (if any) a single AAC audio stream."""
        try:
            out = subprocess.run([self._ffprobe, "-v", "error", "-show_entries",
                                  "stream=codec_type,codec_name:format=duration", "-of", "json", str(path)],
                                 capture_output=True, text=True, timeout=20)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("ffprobe could not run on %s (%s); skipping codec check", path.name, type(e).__name__)
            return None
        if out.returncode != 0:
            raise ClipRejected("ffprobe cannot read this file - it is corrupt or not a real MP4")
        try:
            info = json.loads(out.stdout)
        except ValueError:
            return None
        streams = info.get("streams", [])
        video = [s.get("codec_name") for s in streams if s.get("codec_type") == "video"]
        audio = [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"]
        if not video:
            raise ClipRejected("MP4 has no video stream")
        if video[0] != "h264":
            raise ClipRejected(f"video codec is {video[0]}; WhatsApp only accepts H.264 (re-encode with libx264)")
        if len(audio) > 1:
            raise ClipRejected("MP4 has several audio streams; WhatsApp only accepts one or none")
        if audio and audio[0] != "aac":
            raise ClipRejected(f"audio codec is {audio[0]}; WhatsApp only accepts AAC")
        try:
            return float(info["format"]["duration"])
        except (KeyError, ValueError, TypeError):
            return None
