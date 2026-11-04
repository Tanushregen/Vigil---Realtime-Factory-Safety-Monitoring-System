"""Store a detected violation as a video clip + metadata JSON in B2.

    cam{id}/Violations/{type}/{date}/{time}_{id}.mp4   <- the video clip
    cam{id}/Metadata/{type}/{date}/{time}_{id}.json    <- metadata of that clip

Call record_violation_async() from wherever your detector flags a violation.
It returns immediately; the clip is cut, uploaded and saved in the background
(takes roughly post_seconds + 10 s). record_violation() is the blocking
version if you already run in your own thread.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

from app import clips, recorder, storage
from app.db import SessionLocal, Camera, Violation

logger = logging.getLogger(__name__)


def record_violation(
    camera_id: int,
    violation_type: str,
    confidence: float | None = None,
    description: str | None = None,
    prompt_id: int | None = None,
    extra: dict | None = None,
    pre_seconds: int | None = None,
    post_seconds: int | None = None,
    violation_id: str | None = None,
) -> dict:
    """Cut a clip around "now", upload it with its metadata, save a DB row.

    pre_seconds / post_seconds: seconds of video to keep before / after the
    violation (defaults come from CLIP_PRE_SECONDS / CLIP_POST_SECONDS).
    Blocks for about post_seconds + 10 s.
    """
    occurred_ts = time.time()
    occurred = datetime.fromtimestamp(occurred_ts).astimezone()
    violation_id = violation_id or uuid.uuid4().hex[:8]

    logger.info("[violation] %s: cam%s type=%s detected",
                violation_id, camera_id, violation_type)

    # Camera details (short DB session, closed before we wait for the clip).
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            raise ValueError(f"Camera {camera_id} not found")
        camera_name, camera_zone = camera.name, camera.zone
        cam_pre, cam_post = camera.clip_pre_seconds, camera.clip_post_seconds
    finally:
        db.close()

    # Which 2-minute/2-hour recording chunk is live right now, and how far
    # into it the violation happened (lets the UI jump to the moment).
    recording_ref = None
    seg_start = recorder.get_active_segment_start(camera_id)
    if seg_start:
        offset = occurred.replace(tzinfo=None) - seg_start
        recording_ref = {
            "recording_key": storage.recording_key(camera_id, seg_start),
            "segment_start": seg_start.isoformat(),
            "offset_seconds": round(offset.total_seconds(), 1),
        }

    # Explicit arguments win, then the camera's own setting, then the default.
    pre = pre_seconds if pre_seconds is not None else cam_pre
    post = post_seconds if post_seconds is not None else cam_post
    try:
        clip = clips.cut_clip(camera_id, occurred_ts, pre, post)
    except Exception:
        logger.exception("[violation] %s: FAILED - could not cut the clip",
                         violation_id)
        raise
    clip_path = clip["path"]

    if not clips.camera_exists(camera_id):
        clip_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Camera {camera_id} was deleted while its clip was being made")

    try:
        clip_key = storage.violation_clip_key(
            camera_id, violation_type, occurred, violation_id)
        metadata_key = storage.violation_metadata_key(
            camera_id, violation_type, occurred, violation_id)

        clip_start = datetime.fromtimestamp(clip["start_ts"]).astimezone()
        clip_end = datetime.fromtimestamp(clip["end_ts"]).astimezone()

        metadata = {
            "violation_id": violation_id,
            "camera_id": camera_id,
            "camera_name": camera_name,
            "zone": camera_zone,
            "camera_folder": storage.camera_folder(camera_id),
            "violation_type": violation_type,
            "prompt_id": prompt_id,
            "confidence": confidence,
            "description": description,
            "occurred_at": occurred.isoformat(),
            "occurred_at_utc": occurred.astimezone(timezone.utc).isoformat(),
            "clip": {
                "key": clip_key,
                "start": clip_start.isoformat(),
                "end": clip_end.isoformat(),
                "duration_seconds": clip["duration_seconds"],
                "pre_seconds": clip["pre_seconds"],
                "post_seconds": clip["post_seconds"],
                "size_bytes": clip_path.stat().st_size,
                "content_type": "video/mp4",
            },
            "recording": recording_ref,
            "metadata_key": metadata_key,
            "extra": extra or {},
        }

        logger.info("[violation] %s: uploading clip -> %s",
                    violation_id, clip_key)
        storage.upload_file(str(clip_path), clip_key, "video/mp4",
                            metadata={"camera-id": camera_id,
                                      "violation-id": violation_id})
        storage.upload_json(metadata, metadata_key)

        db = SessionLocal()
        try:
            row = Violation(
                camera_id=camera_id,
                violation_type=storage.slugify(violation_type),
                prompt_id=prompt_id,
                confidence=confidence,
                description=description,
                occurred_at=occurred.replace(tzinfo=None),
                clip_key=clip_key,
                metadata_key=metadata_key,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            metadata["id"] = row.id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        logger.info("[violation] %s: saved (clip=%s, metadata=%s)",
                    violation_id, clip_key, metadata_key)

        clip_path.unlink(missing_ok=True)   # only after a successful upload
        return metadata

    except Exception:
        logger.exception(
            "[violation] %s: FAILED (clip kept locally at %s)",
            violation_id, clip_path)
        raise


def record_violation_async(camera_id: int, violation_type: str,
                           **kwargs) -> str:
    """Fire-and-forget version. Returns the violation id immediately."""
    violation_id = uuid.uuid4().hex[:8]

    def _run():
        try:
            record_violation(camera_id, violation_type,
                             violation_id=violation_id, **kwargs)
        except Exception:
            pass    # already logged in record_violation

    threading.Thread(target=_run, daemon=True,
                     name=f"violation-{violation_id}").start()
    return violation_id