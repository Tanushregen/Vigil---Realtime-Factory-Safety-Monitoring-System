import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from app import storage

folder = sys.argv[1]
vtype = sys.argv[2] if len(sys.argv) > 2 else "no_helmet"
ffmpeg = os.getenv("FFMPEG_BIN", "ffmpeg")

client = storage.get_client()
bucket = storage._bucket()

recordings = [
    k for k in storage.list_keys(f"{folder}/{storage.RECORDINGS_DIR}/")
    if k.endswith(".mp4")
]
if not recordings:
    print(f"No .mp4 recordings found under {folder}/{storage.RECORDINGS_DIR}/")
    sys.exit(1)

source_key = sorted(recordings)[-1]          # newest recording

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / "source.mp4"
    out = Path(tmp) / "compressed.mp4"

    client.download_file(bucket, source_key, str(src))
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)],
        check=True,
    )

    when = datetime.now()
    clip_key = (
        f"{folder}/{storage.VIOLATIONS_DIR}/"
        f"{when:%Y-%m-%d_%H%M%S}_{storage.slugify(vtype)}_{uuid.uuid4().hex[:8]}.mp4"
    )
    storage.upload_file(str(out), clip_key, "video/mp4")

    print(f"Source     : {source_key} ({src.stat().st_size / 1e6:.1f} MB)")
    print(f"Compressed : {out.stat().st_size / 1e6:.1f} MB")
    print(f"Uploaded to: {clip_key}")
