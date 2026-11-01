"""Local media storage — plumbing, not a feature.

Saves bytes under MEDIA_DIR and returns the URL path the app serves them at
(StaticFiles is mounted at /media/files in main.py). Swap for S3 by replacing
this one file.

Local disk is ephemeral on hosts without a persistent volume (free PaaS
tiers): files vanish on redeploy/restart while DB rows keep their URLs. See
DECISIONS.md.
"""

import asyncio
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import BadRequestError

MEDIA_URL_PREFIX = "/media/files"

# Extensions the app itself produces or accepts for storage.
_ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "mp3", "wav", "ogg", "webm", "m4a"}


async def save_media(data: bytes, ext: str) -> str:
    """Persist ``data`` under MEDIA_DIR as <uuid>.<ext>; return its URL path.
    The write runs in a worker thread — multi-MB files must not stall the
    event loop."""
    ext = ext.lower().lstrip(".")
    if ext not in _ALLOWED_EXTS:
        raise BadRequestError(f"unsupported media extension: {ext}")
    if len(data) > settings.MEDIA_MAX_BYTES:
        raise BadRequestError(
            f"media exceeds {settings.MEDIA_MAX_BYTES} bytes", error_code="MEDIA_TOO_LARGE"
        )
    name = f"{uuid.uuid4().hex}.{ext}"
    path = Path(settings.MEDIA_DIR) / name
    await asyncio.to_thread(_write, path, data)
    return f"{MEDIA_URL_PREFIX}/{name}"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
