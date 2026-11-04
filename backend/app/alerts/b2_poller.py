"""Poll a Backblaze B2 S3-compatible bucket for alert JSON and fetch each event's exact MP4."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, List, Optional

from .alert_service import AlertResult, AlertService, Preview
from .config import B2Settings
from .json_parser import AlertInputError, MAX_JSON_BYTES, parse_alert_event, parse_bytes
from .schemas import AlertEvent

log = logging.getLogger("vigil.b2")


@dataclass
class B2PollOutcome:
    json_key: str
    status: str
    event_id: Optional[str] = None
    preview: Optional[Preview] = None
    result: Optional[AlertResult] = None
    error: Optional[str] = None


class B2Poller:
    def __init__(self, client: Any, settings: B2Settings, clips_root: str | os.PathLike):
        self.client = client
        self.settings = settings
        self.clips_root = Path(clips_root).resolve()

    @classmethod
    def from_env(cls, clips_root: str | os.PathLike) -> "B2Poller":
        settings = B2Settings.from_env()
        return cls(settings.create_s3_client(), settings, clips_root)

    def poll_once(self, service: AlertService, *, send: bool = False) -> List[B2PollOutcome]:
        outcomes = []
        for key in self._list_json_keys():
            try:
                outcomes.append(self._process_key(key, service, send=send))
            except Exception as e:
                log.exception("unexpected error processing B2 JSON object %s", key)
                outcomes.append(B2PollOutcome(
                    key, "ERROR", error=f"unexpected error ({type(e).__name__}); see application log"
                ))
        return outcomes

    def process_local_json(
        self, json_path: str | os.PathLike, service: AlertService, *, send: bool = False
    ) -> B2PollOutcome:
        """Use a local event JSON to fetch its referenced B2 clip and preview or send the alert."""
        source = str(Path(json_path))
        try:
            event = parse_alert_event(json_path)
        except AlertInputError as e:
            return B2PollOutcome(source, "REJECTED", error=str(e))
        return self._process_event(source, event, service, send=send)

    def _list_json_keys(self) -> List[str]:
        keys: List[str] = []
        continuation_token = None
        while True:
            request = {"Bucket": self.settings.bucket_name, "Prefix": self.settings.json_prefix}
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            try:
                response = self.client.list_objects_v2(**request)
            except Exception as e:
                raise RuntimeError(f"unable to list B2 objects ({type(e).__name__})") from e

            for item in response.get("Contents", []):
                key = item.get("Key")
                if isinstance(key, str) and key.lower().endswith(".json"):
                    keys.append(key)

            if not response.get("IsTruncated"):
                return sorted(keys)
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                raise RuntimeError("B2 returned a truncated listing without a continuation token")

    def _process_key(self, json_key: str, service: AlertService, *, send: bool) -> B2PollOutcome:
        try:
            raw = self._read_object(json_key, MAX_JSON_BYTES)
        except (ValueError, RuntimeError) as e:
            return B2PollOutcome(json_key, "FAILED", error=str(e))

        try:
            payload = parse_bytes(raw)
            event = AlertEvent.from_raw_dict(payload, source_path=json_key)
        except AlertInputError as e:
            if send:
                result = service.process(raw)
                result.source = json_key
                return B2PollOutcome(json_key, result.status, result.event_id, result=result, error=result.error)
            return B2PollOutcome(json_key, "REJECTED", error=str(e))

        return self._process_event(json_key, event, service, send=send)

    def _process_event(
        self, source: str, event: AlertEvent, service: AlertService, *, send: bool
    ) -> B2PollOutcome:
        if not event.camera_id:
            return B2PollOutcome(source, "REJECTED", event.event_id,
                                 error="B2 event JSON must include a camera_id")
        try:
            self._validate_camera_id(event.camera_id)
        except ValueError as e:
            return B2PollOutcome(source, "REJECTED", event.event_id, error=str(e))
        if not event.clip_path:
            return B2PollOutcome(
                source, "REJECTED", event.event_id,
                error="B2 event JSON must include clip_path (the exact video object key in the bucket)",
            )

        if send:
            existing = service.outbox.get(event.event_id)
            if existing is not None and existing.status == "SENT":
                result = service.process(event)
                result.source = source
                return B2PollOutcome(source, result.status, event.event_id, result=result)

        try:
            video_key = self._validate_video_key(event.clip_path)
            video_data = self._read_object(video_key, service.validator.max_bytes)
            local_path = self._store_clip(event.camera_id, video_key, video_data)
        except (ValueError, RuntimeError) as e:
            return B2PollOutcome(source, "FAILED", event.event_id, error=str(e))

        # Replace the remote object key with a confined local path for the existing clip validator.
        local_event_data = dict(event.data)
        local_event_data.update({
            "event_id": event.event_id,
            "camera_id": event.camera_id,
            "clip_path": local_path,
        })
        local_event = AlertEvent.from_raw_dict(local_event_data, source_path=source)

        if send:
            result = service.process(local_event)
            result.source = source
            return B2PollOutcome(source, result.status, event.event_id, result=result, error=result.error)

        try:
            preview = service.preview(local_event)
        except (AlertInputError, ValueError, OSError) as e:
            return B2PollOutcome(source, "FAILED", event.event_id, error=str(e))
        return B2PollOutcome(source, "PREVIEW", event.event_id, preview=preview)

    def _read_object(self, key: str, max_bytes: int) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.settings.bucket_name, Key=key)
        except Exception as e:
            raise RuntimeError(f"unable to download B2 object {key} ({type(e).__name__})") from e

        body = response["Body"]
        try:
            declared_size = response.get("ContentLength")
            if isinstance(declared_size, int) and declared_size > max_bytes:
                raise ValueError(f"B2 object {key} is larger than the {max_bytes}-byte limit")
            content = body.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError(f"B2 object {key} is larger than the {max_bytes}-byte limit")
            if isinstance(declared_size, int) and len(content) != declared_size:
                raise RuntimeError(f"B2 returned an incomplete object: {key}")
            return content
        finally:
            body.close()

    @staticmethod
    def _validate_video_key(key: str) -> str:
        if not key or "\\" in key or key.startswith("/") or "://" in key:
            raise ValueError("clip_path must be a relative B2 object key, not a URL or local path")
        parts = PurePosixPath(key).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise ValueError("clip_path contains an invalid B2 object path")
        filename = parts[-1]
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.mp4", filename, flags=re.IGNORECASE):
            raise ValueError("B2 video object must have a safe .mp4 file name")
        return key

    @staticmethod
    def _validate_camera_id(camera_id: str) -> None:
        reserved_names = {"CON", "PRN", "AUX", "NUL"}
        reserved_names.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))
        device_name = camera_id.split(".", 1)[0].upper()
        if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", camera_id)
                or camera_id.endswith(".") or device_name in reserved_names):
            raise ValueError("camera_id must be a safe folder name")

    def _store_clip(self, camera_id: str, object_key: str, data: bytes) -> str:
        filename = PurePosixPath(object_key).name
        destination = self.clips_root / camera_id / filename
        destination.parent.mkdir(parents=True, exist_ok=True)

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".b2-", suffix=".part",
                                             delete=False) as temp:
                temp_path = Path(temp.name)
                temp.write(data)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, destination)
        except OSError as e:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"could not cache B2 video {object_key}: {e}") from e

        # The atomic rename means this downloaded file is complete, unlike a local clip still being written.
        return f"{camera_id}/{filename}"


def print_outcome(outcome: B2PollOutcome) -> None:
    print(f"\n[{outcome.status}] {outcome.json_key}"
          + (f"  event={outcome.event_id}" if outcome.event_id else ""))
    if outcome.preview is not None:
        print(outcome.preview.text)
        print(f"clip: {outcome.preview.clip.clip_path} ({outcome.preview.clip.size_bytes} bytes)")
    if outcome.result is not None:
        print(f"WhatsApp result: {outcome.result.status}  ids={outcome.result.message_ids or '-'}"
              + (f"  {outcome.result.error}" if outcome.result.error else ""))
    if outcome.error:
        print(f"  error: {outcome.error}")
