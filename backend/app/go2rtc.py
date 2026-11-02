
import httpx

GO2RTC_API = "http://127.0.0.1:1984"


def register_stream(stream_name: str, rtsp_url: str):
    """
    Register an RTSP camera stream with go2rtc.
    """

    response = httpx.put(
        f"{GO2RTC_API}/api/streams",
        params={
            "name": stream_name,
            "src": rtsp_url
        },
        timeout=10.0
    )

    response.raise_for_status()

    return True


def delete_stream(stream_name: str):
    """
    Remove a registered camera stream from go2rtc.
    """

    response = httpx.delete(
        f"{GO2RTC_API}/api/streams",
        params={
            "src": stream_name
        },
        timeout=10.0
    )

    response.raise_for_status()

    return True
