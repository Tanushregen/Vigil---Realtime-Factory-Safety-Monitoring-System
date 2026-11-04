"""Settings page API: detection values, storage and retention, system status.

Anyone signed in can read. Changing a value, or deleting old files, needs an
administrator.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import retention, settings_store, system_status
from app.auth.dependencies import require_admin

router = APIRouter(prefix="/api", tags=["settings"])

DETECTION_GROUPS = {"detection", "borderline", "scene"}
STORAGE_GROUPS = {"storage"}


class ValuesBody(BaseModel):
    values: dict[str, Any]


class ResetBody(BaseModel):
    keys: Optional[list[str]] = None


def _save(values: dict, groups: set[str]) -> list[dict]:
    try:
        return settings_store.update(values, groups)
    except settings_store.SettingError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------------------------------------------------------------- detection
@router.get("/settings/detection")
def get_detection():
    return {"settings": settings_store.snapshot(DETECTION_GROUPS)}


@router.patch("/settings/detection", dependencies=[Depends(require_admin)])
def patch_detection(body: ValuesBody):
    return {"settings": _save(body.values, DETECTION_GROUPS)}


@router.post("/settings/detection/reset", dependencies=[Depends(require_admin)])
def reset_detection(body: ResetBody):
    try:
        return {"settings": settings_store.reset(body.keys, DETECTION_GROUPS)}
    except settings_store.SettingError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ---------------------------------------------------------------- storage
@router.get("/settings/storage")
def get_storage(refresh: bool = False):
    return {
        "settings": settings_store.snapshot(STORAGE_GROUPS),
        "usage": retention.usage(force=refresh),
        "last_cleanup": retention.last_run(),
    }


@router.patch("/settings/storage", dependencies=[Depends(require_admin)])
def patch_storage(body: ValuesBody):
    return {"settings": _save(body.values, STORAGE_GROUPS)}


@router.get("/settings/storage/preview")
def preview_storage(clip_days: int = Query(0, ge=0, le=3650), recording_days: int = Query(0, ge=0, le=3650)):
    """What would be deleted with these numbers (nothing is deleted)."""
    return retention.preview(clip_days, recording_days)


@router.post("/settings/storage/cleanup", dependencies=[Depends(require_admin)])
def cleanup_storage():
    """Run one cleanup pass now, with the SAVED retention settings."""
    return retention.run_once()


# ---------------------------------------------------------------- system status
@router.get("/system/status")
def get_system_status():
    return system_status.collect()
