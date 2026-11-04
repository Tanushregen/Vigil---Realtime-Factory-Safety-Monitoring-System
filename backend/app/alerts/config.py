"""All tunables in one place. Secrets come from environment variables only - never hard-code them."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Required configuration is missing or malformed (never contains secret values)."""


def _clean_env(name: str, default: str = "") -> str:
    """Read an environment variable and strip inline `# ...` comments safely."""
    raw = os.environ.get(name, default)
    if not raw:
        return default
    return raw.split("#", 1)[0].strip() or default


@dataclass
class WhatsAppConfig:
    token: str
    phone_number_id: str
    recipients: List[str]
    graph_version: str = "v25.0"          # check Meta's docs for the current version (override: WHATSAPP_GRAPH_VERSION)
    use_templates: bool = False           # True for production (see README: 24-hour window)
    template_video: str = "vigil_safety_alert_video"
    template_text: str = "vigil_safety_alert_text"
    template_lang: str = "en"
    alert_template: str = "vigil_violation_alert"   # JSON->WhatsApp alert module: video-header template (see formatter.py)

    @classmethod
    def from_env(cls) -> "WhatsAppConfig":
        missing = [n for n in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "VIGIL_WA_RECIPIENTS")
                   if not _clean_env(n)]
        if missing:
            raise ConfigError("missing required environment variables: " + ", ".join(missing)
                              + " (copy .env.example to .env and fill it in)")
        raw = _clean_env("VIGIL_WA_RECIPIENTS")
        recips = [re.sub(r"\D", "", r) for r in raw.split(",") if r.strip()]
        for r in recips:
            if not 8 <= len(r) <= 15:
                raise ConfigError("recipient numbers must be E.164 digits, e.g. 919876543210")
        return cls(
            token=_clean_env("WHATSAPP_TOKEN"),
            phone_number_id=_clean_env("WHATSAPP_PHONE_NUMBER_ID"),
            recipients=recips,
            graph_version=_clean_env("WHATSAPP_GRAPH_VERSION", "v25.0"),
            use_templates=_clean_env("VIGIL_WA_USE_TEMPLATES", "0") == "1",
            template_lang=_clean_env("VIGIL_WA_TEMPLATE_LANG", "en"),
            alert_template=_clean_env("VIGIL_WA_ALERT_TEMPLATE", "vigil_violation_alert"),
        )


@dataclass(frozen=True)
class B2Settings:
    endpoint_url: str
    application_key_id: str
    application_key: str
    bucket_name: str
    json_prefix: str = "alerts/"

    @classmethod
    def from_env(cls) -> "B2Settings":
        def first(*names: str) -> str:
            for name in names:
                value = _clean_env(name)
                if value:
                    return value
            return ""

        values = {
            "B2_APPLICATION_KEY_ID": first("B2_APPLICATION_KEY_ID", "keyID"),
            "B2_APPLICATION_KEY": first("B2_APPLICATION_KEY", "applicationKey"),
            "B2_BUCKET_NAME": first("B2_BUCKET_NAME"),
            "B2_S3_ENDPOINT_URL": first("B2_S3_ENDPOINT_URL", "B2_ENDPOINT", "Endpoint"),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError("missing required B2 environment variables: " + ", ".join(missing))

        endpoint = values["B2_S3_ENDPOINT_URL"].rstrip("/")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ConfigError("B2_S3_ENDPOINT_URL must be an HTTPS S3 endpoint")

        return cls(
            endpoint_url=endpoint,
            application_key_id=values["B2_APPLICATION_KEY_ID"],
            application_key=values["B2_APPLICATION_KEY"],
            bucket_name=values["B2_BUCKET_NAME"],
            json_prefix=first("B2_JSON_PREFIX") or "alerts/",
        )

    @property
    def region_name(self) -> str:
        host_parts = (urlparse(self.endpoint_url).hostname or "").split(".")
        if len(host_parts) >= 3 and host_parts[0] == "s3":
            return host_parts[1]
        raise ConfigError("B2_S3_ENDPOINT_URL must use the regional S3 endpoint format")

    def create_s3_client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            raise ConfigError("B2 polling requires boto3; install project requirements first") from e

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.application_key_id,
            aws_secret_access_key=self.application_key,
            region_name=self.region_name,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )


def _env_bool(name: str, default: bool) -> bool:
    return _clean_env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass
class AlertSettings:
    """Settings for the JSON -> WhatsApp alert module (no secrets here; those live in WhatsAppConfig)."""
    clips_root: str = "clips"                 # clips must live below this folder
    clip_base_dir: str = "."                  # JSON clip_path (e.g. clips/CAM01/event001.mp4) is resolved against this
    outbox_db: str = "vigil_alert_outbox.db"  # SQLite: dedup, retry queue, rejected payloads
    local_tz: str = "Asia/Kolkata"            # used to display timezone-aware timestamps
    max_attempts: int = 5                     # automatic retries per event before it needs a manual --force
    enforce_clip_name_match: bool = True      # clip file name must carry the event's number (event001.mp4 <-> EVT001)
    check_codecs: bool = True                 # ffprobe H.264/AAC check when ffprobe is installed
    clip_min_age_s: float = 1.0                # local clips younger than this may still be writing
    fallback_to_template: bool = True         # free-form send rejected with 131047 -> retry as approved template
    http_timeout_s: float = 120.0             # uploads of 10+ MB clips need more than a few seconds
    stale_claim_s: float = 600.0              # a SENDING row older than this is assumed to be a crashed worker

    @classmethod
    def from_env(cls) -> "AlertSettings":
        d = cls()
        try:
            return cls(
                clips_root=_clean_env("VIGIL_CLIPS_DIR", d.clips_root),
                clip_base_dir=_clean_env("VIGIL_CLIP_BASE_DIR", d.clip_base_dir),
                outbox_db=_clean_env("VIGIL_ALERT_DB", d.outbox_db),
                local_tz=_clean_env("VIGIL_LOCAL_TZ", d.local_tz),
                max_attempts=int(_clean_env("VIGIL_MAX_ATTEMPTS", str(d.max_attempts))),
                enforce_clip_name_match=_env_bool("VIGIL_ENFORCE_CLIP_NAME_MATCH", d.enforce_clip_name_match),
                check_codecs=_env_bool("VIGIL_CHECK_CODECS", d.check_codecs),
                clip_min_age_s=float(_clean_env("VIGIL_CLIP_MIN_AGE_S", str(d.clip_min_age_s))),
                fallback_to_template=_env_bool("VIGIL_WA_FALLBACK_TO_TEMPLATE", d.fallback_to_template),
                http_timeout_s=float(_clean_env("VIGIL_HTTP_TIMEOUT", str(d.http_timeout_s))),
            )
        except ValueError as e:
            raise ConfigError(f"invalid numeric setting in environment: {e}") from None
