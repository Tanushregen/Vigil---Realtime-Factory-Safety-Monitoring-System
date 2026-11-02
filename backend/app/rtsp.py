
import json
import subprocess
import shutil
from urllib.parse import quote


# RTSP URL templates for supported DVR brands
TEMPLATES = {
    "hikvision": (
        "rtsp://{u}:{p}@{ip}:{port}/Streaming/Channels/{ch}01",
        "rtsp://{u}:{p}@{ip}:{port}/Streaming/Channels/{ch}02"
    ),

    "dahua": (
        "rtsp://{u}:{p}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=0",
        "rtsp://{u}:{p}@{ip}:{port}/cam/realmonitor?channel={ch}&subtype=1"
    )
}

# CP Plus uses the Dahua URL format
TEMPLATES["cpplus"] = TEMPLATES["dahua"]


def build_urls(brand, ip, port, user, pwd, ch):
    """
    Generate main stream and substream URLs
    for a DVR camera channel.
    """

    brand = brand.lower().strip().replace(" ", "").replace("-", "")

    if brand not in TEMPLATES:
        raise ValueError(
            f"Automatic RTSP URL generation is not supported for {brand}"
        )

    main, sub = TEMPLATES[brand]

    # Encode username and password for special characters
    values = {
        "u": quote(user, safe=""),
        "p": quote(pwd, safe=""),
        "ip": ip,
        "port": port,
        "ch": ch
    }

    main_url = main.format(**values)
    sub_url = sub.format(**values)

    return main_url, sub_url


def validate_rtsp_stream(rtsp_url: str) -> dict:
    """
    Validate an RTSP stream using FFprobe.
    Uses TCP transport and a 10-second RTSP timeout.
    """

    if not shutil.which("ffprobe"):
        return {
            "valid": False,
            "error": "FFprobe is not installed"
        }

    command = [
        "ffprobe",
        "-v", "error",
        "-rtsp_transport", "tcp",
        "-timeout", "10000000",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate",
        "-of", "json",
        rtsp_url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20
        )

        if result.returncode != 0:
            error_message = result.stderr.lower()
            if "401" in error_message or "unauthorized" in error_message:
                error = "Wrong username or password"

            elif "connection refused" in error_message:
                error = (
                    "DVR not reachable - check IP, port "
                    "and that RTSP is enabled"
                )

            elif (
                "timed out" in error_message
                or "timeout" in error_message
            ):
                error = (
                    "DVR not reachable - check IP, port "
                    "and that RTSP is enabled"
                )

            elif (
                "404" in error_message
                or "invalid data" in error_message
                or "not found" in error_message
            ):
                error = (
                    "This channel doesn't exist or has no camera"
                )

            else:
                error = "Unable to read RTSP stream"

            return {
                "valid": False,
                "error": error
            }

        stream_data = json.loads(result.stdout)
        streams = stream_data.get("streams", [])

        if not streams:
            return {
                "valid": False,
                "error": "No video stream found"
            }

        video = streams[0]

        # Convert FPS fraction into a number
        fps_value = video.get("avg_frame_rate", "0/1")
        try:
            numerator, denominator = map(
                int, fps_value.split("/")
           )

            fps = (
                round(numerator / denominator)
                if denominator else 0
            )

        except (ValueError, ZeroDivisionError):
            fps = 0
        return {
            "valid": True,
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": fps
        }

    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "error": "RTSP validation timed out"
        }

    except Exception:
        return {
            "valid": False,
            "error": "Unable to validate RTSP stream"
        }
        
