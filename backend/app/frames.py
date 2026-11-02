
import cv2
import threading
import time
import logging

from app.db import SessionLocal, Camera

logger = logging.getLogger(__name__)

# Frame sampling rate: configurable between 1 and 5 FPS
FRAME_FPS = 2

frame_lock = threading.Lock()

# Store the latest JPEG frame for each camera
latest_frames = {}

# Track active camera capture threads
capture_threads = {}

# Control capture threads
stop_events = {}


def capture_camera_frames(camera_id: int, stop_event: threading.Event):
    """Read frames from go2rtc RTSP restream and store the latest JPEG."""

    cap = None

    try:
        rtsp_url = f"rtsp://127.0.0.1:8554/cam{camera_id}"

        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            logger.warning(
                "Could not open stream for camera %s",
                camera_id
            )
            return

        frame_interval = 1 / max(1, min(FRAME_FPS, 5))

        while not stop_event.is_set():
            success, frame = cap.read()

            if not success:
                logger.warning(
                    "Frame capture failed for camera %s",
                    camera_id
                )
                time.sleep(1)
                continue

            success, jpeg = cv2.imencode(
                ".jpg",
                frame,
                [cv2.IMWRITE_JPEG_QUALITY, 80]
            )

            if success:
                with frame_lock:
                    latest_frames[camera_id] = jpeg.tobytes()

            time.sleep(frame_interval)

    except Exception:
        logger.error("Frame capture failed for camera %s", camera_id)

    finally:
        if cap is not None:
            cap.release()

        with frame_lock:
            latest_frames.pop(camera_id, None)

            # Remove references only if this is still the active thread
            if capture_threads.get(camera_id) is threading.current_thread():
                capture_threads.pop(camera_id, None)
                stop_events.pop(camera_id, None)

        logger.info("Stopped frame capture for camera %s", camera_id)


def start_frame_capture():
    """Start frame capture for cameras enabled for analysis."""

    db = SessionLocal()

    try:
        cameras = db.query(Camera).filter(
            Camera.analysis_enabled.is_(True)
        ).all()

        for camera in cameras:
            camera_id = camera.id

            with frame_lock:
                existing_thread = capture_threads.get(camera_id)

                if existing_thread and existing_thread.is_alive():
                    continue

                stop_event = threading.Event()

                thread = threading.Thread(
                    target=capture_camera_frames,
                    args=(camera_id, stop_event),
                    daemon=True,
                    name=f"camera-frame-{camera_id}"
                )

                stop_events[camera_id] = stop_event
                capture_threads[camera_id] = thread

                thread.start()

    finally:
        db.close()


def stop_frame_capture(camera_id: int):
    """Stop frame capture for a camera."""

    with frame_lock:
        stop_event = stop_events.get(camera_id)

        if stop_event:
            stop_event.set()

        latest_frames.pop(camera_id, None)
