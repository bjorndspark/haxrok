"""Append-only sink for browser log batches (POST /api/v1/logs/frontend)."""

from pathlib import Path

from app.core.config import settings
from app.features.logs.schemas import FrontendLogBatch

# The endpoint is unauthenticated: a raw newline in a field would let any
# caller forge whole log lines, so control characters are escaped.
_ESCAPES = str.maketrans({"\n": "\\n", "\r": "\\r", "\t": "\\t"})


def _clean(text: str) -> str:
    return text.translate(_ESCAPES)


def append_frontend_logs(batch: FrontendLogBatch) -> None:
    """Append one batch to FRONTEND_LOG_FILE; no-op when disabled or empty."""
    if not settings.FRONTEND_LOG_FILE or not batch.entries:
        return
    path = Path(settings.FRONTEND_LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    # First 8 chars of the browser session ID (one per tab) distinguish tabs.
    session = _clean(batch.session_id[:8])
    lines = "".join(
        f"{e.ts:%Y-%m-%d %H:%M:%S} | {e.level:<5} | {session} | "
        f"{_clean(e.source)} | {_clean(e.message)}\n"
        for e in batch.entries
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(lines)
