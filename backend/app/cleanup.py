"""Remove everything belonging to a camera that was deleted in the frontend.

Runs in the background so the delete request returns immediately (removing a
folder with many recordings can take a while). Steps, in this order:

  1. recorder.forget_camera(): stop recording, wait for any upload in
     progress, throw away chunks still waiting locally (so they can never be
     uploaded and bring the folder back).
  2. remove leftover local clip files.
  3. delete the camera's whole folder in B2, every stored version included.

All log lines start with "[cleanup]".
"""
import logging
import threading

from app import clips, recorder, storage

logger = logging.getLogger(__name__)


def delete_camera_data(camera_id: int, folder: str | None) -> None:
    logger.info("[cleanup] cam%s: removing local files and B2 folder %r",
                camera_id, folder)

    try:
        recorder.forget_camera(camera_id)
    except Exception:
        logger.exception("[cleanup] cam%s: could not clear local recordings",
                         camera_id)

    try:
        for leftover in clips.CLIPS_DIR.glob(f"cam{camera_id}_*"):
            leftover.unlink(missing_ok=True)
    except Exception:
        logger.exception("[cleanup] cam%s: could not clear local clips",
                         camera_id)

    if not folder:
        logger.info("[cleanup] cam%s: no B2 folder was ever created, "
                    "nothing to delete in the bucket", camera_id)
        return

    try:
        count = storage.delete_prefix(f"{folder}/")
        logger.info("[cleanup] cam%s: deleted B2 folder '%s' (%d object "
                    "version(s))", camera_id, folder, count)
    except Exception:
        logger.exception(
            "[cleanup] cam%s: B2 folder '%s' could NOT be fully deleted. "
            "Remove the rest with: python backend/b2_tools.py "
            "delete-prefix %s/", camera_id, folder, folder)


def delete_camera_data_async(camera_id: int, folder: str | None) -> None:
    threading.Thread(
        target=delete_camera_data, args=(camera_id, folder),
        daemon=True, name=f"cleanup-cam{camera_id}",
    ).start()