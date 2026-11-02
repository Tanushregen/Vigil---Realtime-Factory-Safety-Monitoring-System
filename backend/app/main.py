import asyncio
import httpx
import logging
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from app.frames import latest_frames, frame_lock, start_frame_capture, stop_frame_capture
from app.health import check_camera_health
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import init_db, get_db, DVR, Camera, SessionLocal, CameraGroup, CameraGroupMember, SafetyPrompt
from app.crypto import encrypt_password, decrypt_password
from app.rtsp import build_urls, validate_rtsp_stream
from app.schemas import CameraGroupUpdate, DVRCreate,CameraUpdate, CameraManualCreate, CameraRTSPUpdate, SafetyPromptCreate, SafetyPromptEnabledUpdate, CameraGroupCreate, CameraGroupUpdate, SafetyPromptCreate, SafetyPromptUpdate, SafetyPromptEnabledUpdate
from app.go2rtc import register_stream, delete_stream
from app.frames import capture_threads, stop_events

app = FastAPI(
    title="VIGIL API",
    description="Camera Ingestion and Management System",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database when the application starts
health_task = None

def register_existing_streams():
    db = SessionLocal()

    try:
        cameras = db.query(Camera).all()

        for camera in cameras:
            try:
                if camera.dvr_id is None:
                    # Restore manually added camera
                    rtsp_url = decrypt_password(
                        camera.rtsp_url_encrypted
                    )

                else:
                    # Restore DVR camera
                    dvr = camera.dvr

                    password = decrypt_password(
                        dvr.password_encrypted
                    )

                    _, rtsp_url = build_urls(
                        brand=dvr.brand.lower().strip(),
                        ip=dvr.ip,
                        port=dvr.port,
                        user=dvr.username,
                        pwd=password,
                        ch=camera.channel
                    )

                register_stream(
                    stream_name=f"cam{camera.id}",
                    rtsp_url=rtsp_url
                )

            except Exception:
                camera.status = "offline"
                logger.warning(
                    "Could not restore stream for camera %s",
                    camera.id
                )
        # Save status changes after processing all cameras
        db.commit()

    finally:
        db.close()
async def monitor_go2rtc():
    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    "http://127.0.0.1:1984/api/streams"
                )
                response.raise_for_status()
                streams = response.json()

            db = SessionLocal()

            try:
                cameras = db.query(Camera).all()

                for camera in cameras:
                    stream_name = f"cam{camera.id}"

                    if stream_name not in streams:
                        logger.warning(
                            "Stream missing for camera %s. Restoring...",
                            camera.id
                        )

                        try:
                            if camera.dvr_id is None:
                                rtsp_url = decrypt_password(
                                    camera.rtsp_url_encrypted
                                )
                            else:
                                dvr = camera.dvr
                                password = decrypt_password(
                                    dvr.password_encrypted
                                )

                                _, rtsp_url = build_urls(
                                    brand=dvr.brand.lower().strip(),
                                    ip=dvr.ip,
                                    port=dvr.port,
                                    user=dvr.username,
                                    pwd=password,
                                    ch=camera.channel
                                )

                            register_stream(
                                stream_name=stream_name,
                                rtsp_url=rtsp_url
                            )

                        except Exception:
                            logger.exception(
                                "Could not restore camera %s",
                                camera.id
                            )

            finally:
                db.close()

            start_frame_capture()

        except Exception:
            logger.exception("go2rtc recovery check failed")

        await asyncio.sleep(10)
recovery_task = None                       
@app.on_event("startup")
async def startup():
    global health_task,recovery_task

    init_db()
    register_existing_streams()
    start_frame_capture()
    health_task = asyncio.create_task(check_camera_health())
    recovery_task = asyncio.create_task(monitor_go2rtc())
    
@app.on_event("shutdown")
async def shutdown():
    # Cancel background asyncio tasks
    for task in (health_task, recovery_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # Signal all frame-capture threads to stop
    with frame_lock:
        events = list(stop_events.values())
        threads = list(capture_threads.values())

        for stop_event in events:
            stop_event.set()

    # Wait for capture threads to finish
    for thread in threads:
        if thread.is_alive():
            await asyncio.to_thread(thread.join, 5)
# Home endpoint
@app.get("/")
def home():
    return {
        "message": "Welcome to VIGIL Camera Management System"
    }


# Health check endpoint
@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }


# Add DVR and generate camera records
@app.post("/api/dvrs")
def add_dvr(
    dvr_data: DVRCreate,
    db: Session = Depends(get_db)
):
    brand = dvr_data.brand.lower().strip()

    if brand == "other":
        raise HTTPException(
            status_code=400,
            detail="For Other brands, use the manual camera API."
        )

    try:
        # Encrypt the password before saving
        encrypted_password = encrypt_password(
            dvr_data.password
        )

        # Create DVR database record
        dvr = DVR(
            name=dvr_data.name,
            brand=dvr_data.brand,
            ip=dvr_data.ip,
            port=dvr_data.port,
            username=dvr_data.username,
            password_encrypted=encrypted_password,
            channel_count=dvr_data.channel_count
        )

        db.add(dvr)
        db.flush()

        camera_results = []

        # Generate and validate each camera channel
        for channel in range(1, dvr_data.channel_count + 1):

            main_url, sub_url = build_urls(
                brand=brand,
                ip=dvr_data.ip,
                port=dvr_data.port,
                user=dvr_data.username,
                pwd=dvr_data.password,
                ch=channel
            )

            # Validate the substream
            result = validate_rtsp_stream(sub_url)

            camera = Camera(
                dvr_id=dvr.id,
                channel=channel,
                name=f"{dvr_data.name} - Camera {channel}",
                main_url=None,
                sub_url=None,
                status="online" if result["valid"] else "offline",
                codec=result.get("codec"),
                width=result.get("width"),
                height=result.get("height"),
                fps=result.get("fps")
            )

            db.add(camera)
            db.flush()

            # Register the camera stream with go2rtc
            if result["valid"]:
                try:
                    register_stream(
                        stream_name=f"cam{camera.id}",
                        rtsp_url=sub_url
                    )
                except Exception:
                    camera.status = "offline"
                    logger.warning(
                        "Failed to register camera %s with go2rtc",
                        camera.id
                   )

            camera_results.append({
                "channel": channel,
                "status": camera.status,
                "error": result.get("error"),
                "codec": result.get("codec"),
                "width": result.get("width"),
                "height": result.get("height"),
                "fps": result.get("fps")
            })

            # Stop checking more channels on first-channel 401
            if (
                channel == 1
                and result.get("error") == "Wrong username or password"
            ):
                db.rollback()
                raise HTTPException(
                    status_code=401,
                    detail="Wrong DVR username or password"
             )

        db.commit()

        return {
            "message": "DVR processing completed",
            "dvr_id": dvr.id,
            "name": dvr.name,
            "channel_count": dvr.channel_count,
            "cameras": camera_results
        }

    except HTTPException:
        db.rollback()
        logger.error("Unable to process DVR request")
        raise

    except Exception:
        db.rollback()

        logger.error("Unable to add DVR")

        raise HTTPException(
            status_code=500,
            detail="Unable to add DVR"
        )
@app.post("/api/cameras/manual")
def add_manual_camera(
    camera_data: CameraManualCreate,
    db: Session = Depends(get_db)
):
    rtsp_url = camera_data.rtsp_url.strip()

    # Validate RTSP URL format
    if not rtsp_url.lower().startswith("rtsp://"):
        raise HTTPException(
            status_code=400,
            detail="Invalid RTSP URL. URL must start with rtsp://"
        )

    # Validate the stream using FFprobe
    result = validate_rtsp_stream(rtsp_url)

    if not result["valid"]:
        raise HTTPException(
            status_code=400,
            detail=result["error"]
        )

    try:
        # Create camera record with encrypted RTSP URL
        camera = Camera(
            dvr_id=None,
            channel=1,
            name=camera_data.name,
            zone=camera_data.zone,
            rtsp_url_encrypted=encrypt_password(rtsp_url),
            status="offline",
            codec=result.get("codec"),
            width=result.get("width"),
            height=result.get("height"),
            fps=result.get("fps")
        )

        db.add(camera)
        db.flush()

        # Register stream with go2rtc
        try:
            register_stream(
                stream_name=f"cam{camera.id}",
                rtsp_url=rtsp_url
            )
            camera.status = "online"

        except Exception:
            camera.status = "offline"
            logger.warning(
                "Failed to register manual camera %s with go2rtc",
                camera.id
            )

        db.commit()
        db.refresh(camera)

        return {
            "message": "Manual camera added successfully",
            "camera": {
                "id": camera.id,
                "name": camera.name,
                "zone": camera.zone,
                "status": camera.status,
                "codec": camera.codec,
                "width": camera.width,
                "height": camera.height,
                "fps": camera.fps
            }
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logger.error("Unable to process manual camera request")

        raise HTTPException(
            status_code=500,
            detail="Unable to add manual camera"
        )

# Get all DVR records
@app.get("/api/dvrs")
def get_dvrs(
    db: Session = Depends(get_db)
):
    dvrs = db.query(DVR).all()

    return [
        {
            "id": dvr.id,
            "name": dvr.name,
            "brand": dvr.brand,
            "ip": dvr.ip,
            "port": dvr.port,
            "channel_count": dvr.channel_count,
            "created_at": dvr.created_at,
        }
        for dvr in dvrs
    ]


# Get all cameras, with optional zone filtering
@app.get("/api/cameras")
def get_cameras(
    zone: str | None = None,
    db: Session = Depends(get_db)
):
    query = db.query(Camera)

    if zone:
        query = query.filter(Camera.zone == zone)

    cameras = query.all()

    return [
        {
            "id": camera.id,
            "dvr_id": camera.dvr_id,
            "channel": camera.channel,
            "name": camera.name,
            "zone": camera.zone,
            "codec": camera.codec,
            "width": camera.width,
            "height": camera.height,
            "fps": camera.fps,
            "status": camera.status,
            "last_seen": camera.last_seen,
            "analysis_enabled": camera.analysis_enabled,
        }
        for camera in cameras
    ]
@app.get("/api/cameras/{camera_id}/frame")
def get_camera_frame(camera_id: int, db: Session = Depends(get_db)):

    # Check whether the camera exists
    camera = db.query(Camera).filter(
        Camera.id == camera_id
    ).first()

    if not camera:
        raise HTTPException(
            status_code=404,
            detail="Camera not found"
        )

    # Get the latest captured frame
    with frame_lock:
        frame = latest_frames.get(camera_id)

    if frame is None:
        raise HTTPException(
            status_code=503,
            detail="Frame not available yet"
        )

    return Response(
        content=frame,
        media_type="image/jpeg"
    )
@app.patch("/api/cameras/{camera_id}")
def update_camera(
    camera_id: int,
    camera_data: CameraUpdate,
    db: Session = Depends(get_db)
):
    camera = db.query(Camera).filter(
        Camera.id == camera_id
    ).first()

    if not camera:
        raise HTTPException(
            status_code=404,
            detail="Camera not found"
        )

    update_data = camera_data.model_dump(exclude_unset=True)

    previous_analysis = camera.analysis_enabled

    for field, value in update_data.items():
        setattr(camera, field, value)

    db.commit()
    db.refresh(camera)

    if (
        "analysis_enabled" in update_data
        and previous_analysis != camera.analysis_enabled
    ):
        if camera.analysis_enabled:
            start_frame_capture()
        else:
            stop_frame_capture(camera_id) 
    return {
        "message": "Camera updated successfully",
        "camera_id": camera.id,
        "name": camera.name,
        "zone": camera.zone,
        "analysis_enabled": camera.analysis_enabled
    }
@app.patch("/api/cameras/{camera_id}/rtsp")
def update_camera_rtsp(
    camera_id: int,
    camera_data: CameraRTSPUpdate,
    db: Session = Depends(get_db)
):
    camera = db.query(Camera).filter(
        Camera.id == camera_id
    ).first()

    if not camera:
        raise HTTPException(
            status_code=404,
            detail="Camera not found"
        )

    rtsp_url = camera_data.rtsp_url

    result = validate_rtsp_stream(rtsp_url)

    if not result["valid"]:
        raise HTTPException(
            status_code=400,
            detail=result["error"]
        )

    stream_name = f"cam{camera.id}"
    old_rtsp_url = None

    try:
        # Get the previous RTSP URL for recovery
        if camera.rtsp_url_encrypted:
            old_rtsp_url = decrypt_password(
                camera.rtsp_url_encrypted
            )
        elif camera.dvr:
            dvr = camera.dvr
            password = decrypt_password(dvr.password_encrypted)

            _, old_rtsp_url = build_urls(
                brand=dvr.brand,
                ip=dvr.ip,
                port=dvr.port,
                user=dvr.username,
                pwd=password,
                ch=camera.channel
            )

        # Register the new stream first
        register_stream(
            stream_name=stream_name,
            rtsp_url=rtsp_url
        )

        # Update encrypted URL and metadata
        camera.rtsp_url_encrypted = encrypt_password(rtsp_url)
        camera.codec = result.get("codec")
        camera.width = result.get("width")
        camera.height = result.get("height")
        camera.fps = result.get("fps")

        db.commit()
        
        return {
            "message": "Camera RTSP URL updated successfully",
            "camera_id": camera.id,
            "name": camera.name
        }

    except Exception:
        db.rollback()

        # Restore the previous stream if the update failed
        try:
            if old_rtsp_url:
                register_stream(stream_name, old_rtsp_url)
            else:
                delete_stream(stream_name)
        except Exception:
            logger.warning(
                "Could not restore previous stream for camera %s",
                camera_id
            )

        logger.warning(
            "RTSP update failed for camera %s",
            camera_id
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to update camera RTSP URL"
        )
    
@app.delete("/api/cameras/{camera_id}")
def delete_camera(
    camera_id: int,
    db: Session = Depends(get_db)
):
    camera = db.query(Camera).filter(
        Camera.id == camera_id
    ).first()

    if not camera:
        raise HTTPException(
            status_code=404,
            detail="Camera not found"
        )

    # Stop frame capture for this camera
    stop_frame_capture(camera_id)

    # Remove the camera stream from go2rtc
    try:
        delete_stream(f"cam{camera_id}")
    except Exception:
        logger.warning(
            "Could not remove go2rtc stream for camera %s",
            camera_id
        )

    # Delete camera from database
    try:
        db.delete(camera)
        db.commit()
    except Exception:
        db.rollback()
        logger.error("Failed to delete camera %s from database", camera_id)
        raise HTTPException(
            status_code=500,
            detail="Unable to delete camera"
        )

    return {
        "message": "Camera deleted successfully",
        "camera_id": camera_id
    }
# ---------------- CAMERA GROUP APIs ----------------

@app.get("/api/groups")
def get_groups(db: Session = Depends(get_db)):
    groups = db.query(CameraGroup).all()

    return [
        {
            "id": group.group_code,
            "name": group.name,
            "cameraIds": [member.camera_id for member in group.members],
        }
        for group in groups
    ]


@app.post("/api/groups")
def create_group(
    data: CameraGroupCreate,
    db: Session = Depends(get_db)
):
    camera_ids = list(dict.fromkeys(data.camera_ids))

    cameras = db.query(Camera).filter(
        Camera.id.in_(camera_ids)
    ).all()

    if len(cameras) != len(camera_ids):
        raise HTTPException(status_code=404, detail="One or more cameras not found")

    group = CameraGroup(
        name=data.name.strip(),
        group_code="TEMP"
    )

    db.add(group)
    db.flush()

    group.group_code = f"GROUP-{group.id:02d}"

    for camera_id in camera_ids:
        db.add(CameraGroupMember(
            group_id=group.id,
            camera_id=camera_id
        ))

    db.commit()
    db.refresh(group)

    return {
        "id": group.group_code,
        "name": group.name,
        "cameraIds": camera_ids
    }


@app.patch("/api/groups/{group_code}")
def update_group(
    group_code: str,
    data: CameraGroupUpdate,
    db: Session = Depends(get_db)
):
    group = db.query(CameraGroup).filter(
        CameraGroup.group_code == group_code
    ).first()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    camera_ids = list(dict.fromkeys(data.camera_ids))

    cameras = db.query(Camera).filter(
        Camera.id.in_(camera_ids)
    ).all()

    if len(cameras) != len(camera_ids):
        raise HTTPException(status_code=404, detail="One or more cameras not found")

    group.name = data.name.strip()
    group.members.clear()

    for camera_id in camera_ids:
        group.members.append(
            CameraGroupMember(camera_id=camera_id)
        )

    db.commit()
    db.refresh(group)

    return {
        "id": group.group_code,
        "name": group.name,
        "cameraIds": camera_ids
    }


@app.delete("/api/groups/{group_code}")
def delete_group(
    group_code: str,
    db: Session = Depends(get_db)
):
    group = db.query(CameraGroup).filter(
        CameraGroup.group_code == group_code
    ).first()

    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    db.delete(group)
    db.commit()

    return {"message": "Group deleted successfully"}


# ---------------- SAFETY PROMPT APIs ----------------

def get_prompt_target(prompt_type: str, target: str | None, db: Session):
    if prompt_type == "universal":
        return None, None

    if not target:
        raise HTTPException(
            status_code=400,
            detail="A target is required for group and individual prompts"
        )

    if prompt_type == "group":
        group = db.query(CameraGroup).filter(
            CameraGroup.group_code == target
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Camera group not found")

        return group.id, None

    if prompt_type == "individual":
        try:
            camera_id = int(target)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid camera ID")

        camera = db.query(Camera).filter(
            Camera.id == camera_id
        ).first()

        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        return None, camera_id

    raise HTTPException(status_code=400, detail="Invalid prompt type")


def format_prompt(prompt):
    if prompt.prompt_type == "group":
        target = prompt.group.group_code if prompt.group else None
    elif prompt.prompt_type == "individual":
        target = str(prompt.camera_id) if prompt.camera_id else None
    else:
        target = None

    return {
        "id": prompt.id,
        "title": prompt.title,
        "text": prompt.text,
        "type": prompt.prompt_type,
        "target": target,
        "enabled": prompt.enabled
    }


@app.get("/api/prompts")
def get_prompts(db: Session = Depends(get_db)):
    prompts = db.query(SafetyPrompt).all()
    return [format_prompt(prompt) for prompt in prompts]


@app.post("/api/prompts")
def create_prompt(
    data: SafetyPromptCreate,
    db: Session = Depends(get_db)
):
    group_id, camera_id = get_prompt_target(
        data.type, data.target, db
    )

    prompt = SafetyPrompt(
        title=data.title.strip(),
        text=data.text.strip(),
        prompt_type=data.type,
        group_id=group_id,
        camera_id=camera_id,
        enabled=True
    )

    db.add(prompt)
    db.commit()
    db.refresh(prompt)

    return format_prompt(prompt)


@app.patch("/api/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    data: SafetyPromptUpdate,
    db: Session = Depends(get_db)
):
    prompt = db.query(SafetyPrompt).filter(
        SafetyPrompt.id == prompt_id
    ).first()

    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    group_id, camera_id = get_prompt_target(
        data.type, data.target, db
    )

    prompt.title = data.title.strip()
    prompt.text = data.text.strip()
    prompt.prompt_type = data.type
    prompt.group_id = group_id
    prompt.camera_id = camera_id

    db.commit()
    db.refresh(prompt)

    return format_prompt(prompt)


@app.patch("/api/prompts/{prompt_id}/enabled")
def update_prompt_enabled(
    prompt_id: int,
    data: SafetyPromptEnabledUpdate,
    db: Session = Depends(get_db)
):
    prompt = db.query(SafetyPrompt).filter(
        SafetyPrompt.id == prompt_id
    ).first()

    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    prompt.enabled = data.enabled

    db.commit()
    db.refresh(prompt)

    return format_prompt(prompt)


@app.delete("/api/prompts/{prompt_id}")
def delete_prompt(
    prompt_id: int,
    db: Session = Depends(get_db)
):
    prompt = db.query(SafetyPrompt).filter(
        SafetyPrompt.id == prompt_id
    ).first()

    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    db.delete(prompt)
    db.commit()

    return {"message": "Prompt deleted successfully"}   