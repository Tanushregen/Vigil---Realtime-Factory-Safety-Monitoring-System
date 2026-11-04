"""Backblaze B2 (S3-compatible) storage helpers for VIGIL.

Every camera gets a folder named after the camera name the user typed in the
frontend, with three sub-folders:

    {Camera_Name}/Recordings/{YYYY-MM-DD}/{HH-MM-SS}.mp4
    {Camera_Name}/Violations/{violation_type}/{YYYY-MM-DD}/{HHMMSS}_{id}.mp4
    {Camera_Name}/Metadata/{violation_type}/{YYYY-MM-DD}/{HHMMSS}_{id}.json

B2 has no real folders; they are key prefixes. To make the three folders
visible before any file exists, a hidden zero-byte ".bzEmpty" file is placed in
each one. That is the same convention the B2 web console uses. (Empty
objects whose key ends in "/" make some B2 apps show endless nested folders,
so we never create those.)
"""
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

RECORDINGS_DIR = "Recordings"
VIOLATIONS_DIR = "Violations"
METADATA_DIR = "Metadata"
PLACEHOLDER = ".bzEmpty"

_client = None
_folder_lock = threading.Lock()
_ready_folders: set[str] = set()

# Large files use multipart uploads.
_TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=64 * 1024 * 1024,
    multipart_chunksize=64 * 1024 * 1024,
    max_concurrency=4,
)


def _bucket() -> str:
    return os.environ["B2_BUCKET"]


def get_client():
    """Lazily create a shared boto3 client (boto3 clients are thread-safe)."""
    global _client
    if _client is None:
        endpoint = os.environ["B2_ENDPOINT_URL"]
        match = re.search(r"s3\.([^.]+)\.backblazeb2\.com", endpoint)
        region = os.getenv("B2_REGION") or (match.group(1) if match else None)

        _client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=os.environ["B2_KEY_ID"],
            aws_secret_access_key=os.environ["B2_APP_KEY"],
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
    return _client


# ---------------- names ----------------

def slugify(value: str) -> str:
    """Make a violation type safe to use as a folder name."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unknown"


def clean_folder_name(name: str) -> str:
    """Camera name -> safe folder name. Keeps letters, digits, '.', '-', '_'
    (any language); everything else, including '/', becomes '_'."""
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned[:60]


def camera_folder(camera_id: int) -> str:
    """Folder name for a camera, taken from the name set in the frontend.

    The name is saved on the camera the first time it is needed, so renaming
    the camera later does NOT move its footage (B2 cannot rename folders
    without copying every file). If two cameras end up with the same name,
    the second one gets its id appended, e.g. 'Gate_7'.
    """
    # Imported here to avoid a circular import at module load time.
    from app.db import SessionLocal, Camera

    with _folder_lock:
        db = SessionLocal()
        try:
            camera = db.query(Camera).filter(Camera.id == camera_id).first()
            if camera is None:
                logger.warning("camera_folder: camera %s not found, using "
                               "'cam%s'", camera_id, camera_id)
                return f"cam{camera_id}"

            if camera.storage_folder:
                return camera.storage_folder

            base = clean_folder_name(camera.name or "") or f"cam{camera_id}"
            taken = {
                row[0].lower()
                for row in db.query(Camera.storage_folder).filter(
                    Camera.id != camera_id,
                    Camera.storage_folder.isnot(None),
                ).all()
            }
            folder = base if base.lower() not in taken \
                else f"{base}_{camera_id}"

            camera.storage_folder = folder
            db.commit()
            logger.info("Camera %s (%r) will use B2 folder '%s'",
                        camera_id, camera.name, folder)
            return folder
        finally:
            db.close()


# ---------------- key builders ----------------

def recording_key(camera_id: int, start: datetime) -> str:
    # <camera>/Recordings/2026-11-03_12-47-51.mp4
    return f"{camera_folder(camera_id)}/{RECORDINGS_DIR}/{start:%Y-%m-%d_%H-%M-%S}.mp4"


def _violation_name(when: datetime, violation_type: str, violation_id: str) -> str:
    # 2026-11-03_123456_no_helmet_ab12cd34
    return f"{when:%Y-%m-%d_%H%M%S}_{slugify(violation_type)}_{violation_id}"


def violation_clip_key(camera_id: int, violation_type: str,
                       when: datetime, violation_id: str) -> str:
    return (
        f"{camera_folder(camera_id)}/{VIOLATIONS_DIR}/"
        f"{_violation_name(when, violation_type, violation_id)}.mp4"
    )


def violation_metadata_key(camera_id: int, violation_type: str,
                           when: datetime, violation_id: str) -> str:
    return (
        f"{camera_folder(camera_id)}/{METADATA_DIR}/"
        f"{_violation_name(when, violation_type, violation_id)}.json"
    )


# ---------------- folders ----------------

def ensure_camera_folders(camera_id: int) -> None:
    """Create the camera folder with Recordings/, Violations/ and Metadata/
    (hidden .bzEmpty placeholders). Runs once per folder per process; raises
    if an upload fails so the caller can retry."""
    folder = camera_folder(camera_id)

    with _folder_lock:
        if folder in _ready_folders:
            return

    for name in (RECORDINGS_DIR, VIOLATIONS_DIR, METADATA_DIR):
        get_client().put_object(
            Bucket=_bucket(),
            Key=f"{folder}/{name}/{PLACEHOLDER}",
            Body=b"",
            ContentType="application/octet-stream",
        )

    with _folder_lock:
        _ready_folders.add(folder)
    logger.info("Created B2 folders: %s/{%s, %s, %s}", folder,
                RECORDINGS_DIR, VIOLATIONS_DIR, METADATA_DIR)


# ---------------- upload / download ----------------

def upload_file(local_path: str, key: str,
                content_type: str = "application/octet-stream",
                metadata: dict | None = None) -> None:
    extra = {"ContentType": content_type}
    if metadata:
        extra["Metadata"] = {k: str(v) for k, v in metadata.items()}

    get_client().upload_file(
        local_path, _bucket(), key,
        ExtraArgs=extra, Config=_TRANSFER_CONFIG,
    )
    logger.info("Uploaded %s", key)


def upload_bytes(data: bytes, key: str,
                 content_type: str = "application/octet-stream") -> None:
    get_client().put_object(
        Bucket=_bucket(), Key=key, Body=data, ContentType=content_type
    )
    logger.info("Uploaded %s", key)


def upload_json(obj: dict, key: str) -> None:
    upload_bytes(
        json.dumps(obj, indent=2, default=str).encode("utf-8"),
        key,
        "application/json",
    )


def presigned_url(key: str, expires: int = 3600) -> str:
    """Temporary URL so the UI can play a private object."""
    return get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expires,
    )


def list_keys(prefix: str = "") -> list[str]:
    keys = []
    paginator = get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def delete_prefix(prefix: str) -> int:
    """Permanently delete everything under a folder, including every stored
    version and delete marker, so no hidden copies keep using space.
    Returns the number of object versions removed."""
    prefix = prefix.strip()
    if not prefix.endswith("/"):
        prefix += "/"
    if len(prefix) < 2 or prefix in ("./", "../"):
        raise ValueError(f"Refusing to delete prefix {prefix!r}")

    client = get_client()
    bucket = _bucket()

    targets: list[tuple[str, str | None]] = []
    try:
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                targets.append((item["Key"], item["VersionId"]))
    except Exception:
        logger.warning("Could not list versions under %s, deleting current "
                       "files only", prefix, exc_info=True)
        targets = [(k, None) for k in list_keys(prefix)]

    def _delete(target):
        key, version_id = target
        if version_id:
            client.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
        else:
            client.delete_object(Bucket=bucket, Key=key)

    failures = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(_delete, t) for t in targets]:
            try:
                future.result()
            except Exception:
                failures += 1
                logger.exception("Could not delete an object under %s", prefix)

    if failures:
        raise RuntimeError(
            f"{failures} of {len(targets)} objects under {prefix} could not "
            "be deleted")

    logger.info("Deleted %d object version(s) under %s", len(targets), prefix)
    return len(targets)