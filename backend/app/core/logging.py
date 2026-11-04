import logging
import shutil
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path

from app.core.clock import get_clock
from app.core.config import settings

LOG_FORMAT_DEV = (
    "%(asctime)s | %(levelname)-8s | %(request_id)s | "
    "%(name)s:%(funcName)s:%(lineno)d | %(message)s"
)
LOG_FORMAT_PROD = "%(asctime)s | %(levelname)-8s | %(request_id)s | %(name)s | %(message)s"

# Request-scoped ID for log correlation, stamped on error bodies too.
# Set/cleared by middleware; read by the filter below and app/errors.py.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return request_id_ctx.get()


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex[:8]
    request_id_ctx.set(rid)
    return rid


def clear_request_id() -> None:
    request_id_ctx.set("-")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def setup_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt = LOG_FORMAT_DEV if settings.ENVIRONMENT == "development" else LOG_FORMAT_PROD

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)

    if settings.LOG_FILE:
        try:
            path = Path(settings.LOG_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_existing_log(path)
            file_handler = logging.FileHandler(path)
        except OSError:
            logging.getLogger(__name__).warning(
                "Cannot write log file %s; stdout only", settings.LOG_FILE
            )
        else:
            file_handler.setLevel(level)
            file_handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))
            file_handler.addFilter(RequestContextFilter())
            root.addHandler(file_handler)
            # The "uvicorn" logger has propagate=False so it never reaches
            # root; attach the file handler directly to capture startup and
            # error logs ("uvicorn.error" propagates up to "uvicorn").
            uv_logger = logging.getLogger("uvicorn")
            for h in list(uv_logger.handlers):
                if getattr(h, "_app_file", False):
                    uv_logger.removeHandler(h)
            file_handler._app_file = True  # type: ignore[attr-defined]
            uv_logger.addHandler(file_handler)

    # Browser logs are appended to FRONTEND_LOG_FILE by the ingest endpoint;
    # rotate whatever the last session left so app.log is per-session too.
    if settings.FRONTEND_LOG_FILE:
        try:
            _rotate_existing_log(Path(settings.FRONTEND_LOG_FILE))
        except OSError:
            logging.getLogger(__name__).warning(
                "Cannot rotate frontend log %s", settings.FRONTEND_LOG_FILE
            )

    for name in ("uvicorn.access", "httpx", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _rotate_existing_log(path: Path) -> None:
    """Archive a non-empty log file on startup: app.log moves to
    archive/<YYYYMMDD>/<name>.<YYYYMMDD_HH>. If that hour's archive already
    exists the old log is appended to it, so frequent --reload restarts can
    never flood the archive. Empty logs are never archived."""
    if not path.exists() or path.stat().st_size == 0:
        return
    now = get_clock().now()
    archive_dir = path.parent / "archive" / now.strftime("%Y%m%d")
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{path.name}.{now.strftime('%Y%m%d_%H')}"
    if target.exists():
        with target.open("ab") as dst, path.open("rb") as src:
            shutil.copyfileobj(src, dst)
        path.unlink()
    else:
        path.rename(target)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
