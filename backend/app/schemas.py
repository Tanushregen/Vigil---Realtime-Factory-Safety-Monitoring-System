
from typing import Optional
from typing import Literal
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.rtsp import SUPPORTED_SCHEMES


ALLOWED_ZONES = [
    "Shop floor",
    "Press area",
    "Gate",
    "Storage",
    "Fire exit",
    "Office",
    "Other"
]

ALLOWED_BRANDS = ["hikvision", "dahua", "cp plus", "other"]


def validate_name(value):
    if not value or not value.strip():
        raise ValueError("Name cannot be empty")
    return value.strip()


def validate_zone(value):
    if value is not None and value not in ALLOWED_ZONES:
        raise ValueError("Invalid camera zone")
    return value


def validate_rtsp_url(value):
    value = value.strip()

    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in SUPPORTED_SCHEMES
            or not parsed.hostname
            or not parsed.port is None and not (1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Enter a valid stream URL (rtsp, rtsps, rtmp, rtmps, http, https or srt)"
        )

    return value


class DVRCreate(BaseModel):
    name: str = Field(min_length=1)
    brand: str
    ip: str
    port: int = Field(default=554, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    channel_count: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return validate_name(value)

    @field_validator("brand")
    @classmethod
    def check_brand(cls, value):
        value = value.strip().lower()
        if value not in ALLOWED_BRANDS:
            raise ValueError("Unsupported DVR brand")
        return value

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, value):
        try:
            return str(ip_address(value.strip()))
        except ValueError:
            raise ValueError("Enter a valid IP address")


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    zone: Optional[str] = None
    analysis_enabled: Optional[bool] = None
    
    segment_seconds: int | None = Field(default=None, ge=10, le=86400)
    clip_pre_seconds: int | None = Field(default=None, ge=0, le=60)
    clip_post_seconds: int | None = Field(default=None, ge=0, le=60)

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        if value is not None:
            return validate_name(value)
        return value

    @field_validator("zone")
    @classmethod
    def check_zone(cls, value):
        return validate_zone(value)


class CameraRTSPUpdate(BaseModel):
    rtsp_url: str = Field(min_length=1)

    @field_validator("rtsp_url")
    @classmethod
    def check_rtsp_url(cls, value):
        return validate_rtsp_url(value)


class CameraManualCreate(BaseModel):
    name: str = Field(min_length=1)
    rtsp_url: str
    zone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def check_name(cls, value):
        return validate_name(value)

    @field_validator("rtsp_url")
    @classmethod
    def check_rtsp_url(cls, value):
        return validate_rtsp_url(value)

    @field_validator("zone")
    @classmethod
    def check_zone(cls, value):
        return validate_zone(value)
class CameraGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    camera_ids: list[int] = Field(min_length=1)


class CameraGroupUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    camera_ids: list[int] = Field(min_length=1)


class SafetyPromptCreate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1)
    type: Literal["universal", "group", "individual"]
    target: str | None = None


class SafetyPromptUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1)
    type: Literal["universal", "group", "individual"]
    target: str | None = None


class SafetyPromptEnabledUpdate(BaseModel):
    enabled: bool