"""Detector settings, read from the environment / the project's .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env sits in the project root (same place crypto.py reads it from).
load_dotenv(Path(__file__).resolve().parents[3] / ".env")


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# --- Meta model API (OpenAI-compatible chat/completions) ---------------------
META_API_KEY = _str("META_API_KEY") or _str("META_LLAMA_API_KEY")
META_API_URL = (_str("META_API_URL") or _str("META_LLAMA_API_URL")
                or "https://api.llama.com/compat/v1").rstrip("/")
META_MODEL = (_str("META_MODEL") or _str("META_LLAMA_MODEL")
              or "Llama-4-Maverick-17B-128E-Instruct-FP8")
META_TIMEOUT = _float("META_TIMEOUT_SECONDS", 25.0)
META_RETRIES = _int("META_RETRIES", 2)
# Max Meta API calls running at the same time across ALL cameras.
META_MAX_PARALLEL = _int("META_MAX_PARALLEL", 4)

# --- Local pose model + tracker ----------------------------------------------
# Weights live in backend/models/ and are downloaded there on first use.
_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
_MODELS_DIR.mkdir(exist_ok=True)
POSE_MODEL = _str("DETECTOR_POSE_MODEL", str(_MODELS_DIR / "yolo11n-pose.pt"))
POSE_DEVICE = _str("DETECTOR_DEVICE", "")                      # "" = auto (GPU if any)
POSE_IMGSZ = _int("DETECTOR_IMGSZ", 640)
POSE_CONF = _float("DETECTOR_CONF", 0.35)
DETECT_FPS = _float("DETECTOR_FPS", 4.0)    # max local detections/second per camera

# --- Optional: SAM 2.1 cuts each person out before the cloud model sees the crop ---
# Off by default. Weights download to backend/models/ on first use.
SEGMENT = _str("DETECTOR_SEGMENT", "0") in ("1", "true", "yes")
SAM_MODEL = _str("DETECTOR_SAM_MODEL", str(_MODELS_DIR / "sam2.1_t.pt"))
SEGMENT_DIM = _float("DETECTOR_SEGMENT_DIM", 0.2)   # brightness kept outside the person

# --- Scene log: zones, dwell time, PPE and gate state, saved to the database ---
SCENE_LOG = _str("SCENE_LOG", "1") in ("1", "true", "yes")
SCENE_PPE = [item.strip().lower() for item in _str("SCENE_PPE", "helmet,vest").split(",")
             if item.strip()]

# --- When the cloud model is called -------------------------------------------
SCAN_INTERVAL = _float("DETECTOR_SCAN_INTERVAL", 3.0)   # people in view: every N s
IDLE_INTERVAL = _float("DETECTOR_IDLE_INTERVAL", 10.0)  # nobody in view: every N s
EVENT_MIN_GAP = _float("DETECTOR_EVENT_MIN_GAP", 1.0)   # new person / posture change
CONFIRM_RESCAN = _float("DETECTOR_CONFIRM_RESCAN", 1.0) # re-check soon after a hit
MAX_TARGETS = _int("DETECTOR_MAX_TARGETS", 6)           # people sent per scan
IDLE_MOTION_THRESHOLD = _float("DETECTOR_IDLE_MOTION", 4.0)  # mean pixel diff

# --- Confirmation / duplicate suppression -------------------------------------
CONFIRM_HITS = _int("DETECTOR_CONFIRM_HITS", 2)         # K positive results ...
CONFIRM_WINDOW = _int("DETECTOR_CONFIRM_WINDOW", 3)     # ... out of the last N
MIN_CONFIDENCE = _int("DETECTOR_MIN_CONFIDENCE", 60)    # 0-100, VLM confidence floor
COOLDOWN_SECONDS = _float("DETECTOR_COOLDOWN", 60.0)

# --- Local fall alert (no cloud call). Off by default: only user rules fire. --
FALL_DETECTION = _str("DETECTOR_FALL_DETECTION", "0") in ("1", "true", "yes")
FALL_SECONDS = _float("DETECTOR_FALL_SECONDS", 1.5)

# --- Image sizes sent to the cloud model --------------------------------------
FRAME_MAX_SIDE = _int("DETECTOR_FRAME_MAX_SIDE", 1280)
CROP_MAX_SIDE = _int("DETECTOR_CROP_MAX_SIDE", 512)
JPEG_QUALITY = _int("DETECTOR_JPEG_QUALITY", 85)
