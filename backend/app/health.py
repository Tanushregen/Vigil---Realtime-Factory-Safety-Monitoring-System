
import asyncio
import logging
from datetime import datetime

import httpx

from app.db import SessionLocal, Camera


logger = logging.getLogger(__name__)

GO2RTC_API = "http://127.0.0.1:1984"


async def check_camera_health():
    """
    Check camera health every 20 seconds using go2rtc JPEG frames.
    """

    while True:
        db = SessionLocal()

        try:
            cameras = db.query(Camera).all()

            async with httpx.AsyncClient(timeout=5.0) as client:

                for camera in cameras:
                    previous_status = camera.status

                    try:
                        response = await client.get(
                            f"{GO2RTC_API}/api/frame.jpeg",
                            params={
                                "src": f"cam{camera.id}"
                            }
                        )

                        response.raise_for_status()

                        if (
                            response.headers.get("content-type", "")
                            .startswith("image/jpeg")
                            and response.content
                        ):
                            camera.status = "online"
                            camera.last_seen = datetime.utcnow()
                            camera.fail_count = 0
                        else:
                            raise ValueError("Invalid frame response")

                    except Exception:
                        camera.fail_count = (camera.fail_count or 0) + 1

                        if camera.fail_count >= 2:
                            camera.status = "offline"

                    if previous_status != camera.status:

                        if camera.status == "online":
                            logger.info(
                                "Camera %s is online or recovered",
                                camera.id
                            )

                        elif camera.status == "offline":
                            logger.warning(
                                "Camera %s changed to offline",
                                camera.id
                            )

            db.commit()

        except Exception:
            db.rollback()
            logger.exception("Camera health monitoring failed")

        finally:
            db.close()

        await asyncio.sleep(20)
