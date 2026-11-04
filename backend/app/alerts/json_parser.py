"""Read and parse the event/violation JSON coming from the detection backend."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from .schemas import AlertEvent

MAX_JSON_BYTES = 64 * 1024

Source = Union[str, bytes, bytearray, Mapping[str, Any], "os.PathLike[str]"]


class AlertInputError(ValueError):
    """The incoming alert can never be processed as received (fix the JSON and resubmit)."""


class InvalidAlertJson(AlertInputError):
    """Not parseable JSON, or not a JSON object."""


class AlertValidationError(AlertInputError):
    """Valid JSON, but fields have invalid values or payload is empty."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _reject_constant(name: str):
    raise ValueError(f"invalid JSON constant {name}")      # NaN / Infinity are not valid JSON


def parse_json_text(text: str) -> Dict[str, Any]:
    if len(text.encode("utf-8", "ignore")) > MAX_JSON_BYTES:
        raise InvalidAlertJson(f"JSON is larger than {MAX_JSON_BYTES // 1024} KB")
    if not text.strip():
        raise InvalidAlertJson("empty JSON payload")
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise InvalidAlertJson(f"malformed JSON: {e.msg} (line {e.lineno}, column {e.colno})") from None
    except ValueError as e:
        raise InvalidAlertJson(str(e)) from None
    if not isinstance(data, dict):
        raise InvalidAlertJson("top-level JSON value must be an object")
    return data


def read_json_file(path: Union[str, "os.PathLike[str]"]) -> Dict[str, Any]:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_JSON_BYTES:
            raise InvalidAlertJson(f"JSON file is larger than {MAX_JSON_BYTES // 1024} KB")
        raw = p.read_bytes()
    except FileNotFoundError:
        raise InvalidAlertJson(f"JSON file not found: {p}") from None
    except IsADirectoryError:
        raise InvalidAlertJson(f"expected a JSON file, got a directory: {p}") from None
    except OSError as e:
        raise InvalidAlertJson(f"cannot read JSON file {p}: {e.strerror or e}") from None
    return parse_bytes(raw)


def parse_bytes(raw: Union[bytes, bytearray]) -> Dict[str, Any]:
    try:
        return parse_json_text(bytes(raw).decode("utf-8-sig"))     # tolerate a UTF-8 BOM (Windows editors)
    except UnicodeDecodeError:
        raise InvalidAlertJson("payload is not valid UTF-8") from None


def _load_event_dict_with_origin(source: Source) -> Tuple[Dict[str, Any], Optional[str]]:
    """Return (parsed_dict, source_file_path_if_any)."""
    if isinstance(source, Mapping):
        return copy.deepcopy(dict(source)), None
    if isinstance(source, (bytes, bytearray)):
        return parse_bytes(source), None
    if isinstance(source, os.PathLike):
        p = Path(source)
        return read_json_file(p), str(p.resolve())
    if isinstance(source, str):
        if not source.strip() or source.lstrip().startswith(("{", "[")):
            return parse_json_text(source), None
        p = Path(source)
        return read_json_file(p), str(p.resolve())
    raise InvalidAlertJson(f"unsupported input type: {type(source).__name__}")


def load_event_dict(source: Source) -> Dict[str, Any]:
    """dict -> copy | bytes -> parsed | Path -> file | str starting with '{' -> JSON text, any other str -> file path."""
    data, _ = _load_event_dict_with_origin(source)
    return data


def parse_alert_event(source: Source) -> AlertEvent:
    """Source -> validated AlertEvent. Raises InvalidAlertJson / AlertValidationError (both AlertInputError)."""
    if isinstance(source, AlertEvent):
        return source
    data, source_path = _load_event_dict_with_origin(source)
    return AlertEvent.from_raw_dict(data, source_path=source_path)
