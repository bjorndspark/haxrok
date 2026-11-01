from fastapi import APIRouter

from app.core.responses import SuccessResponse, ok
from app.features.logs import service
from app.features.logs.schemas import FrontendLogBatch

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post("/frontend")
def ingest_frontend_logs(batch: FrontendLogBatch) -> SuccessResponse[None]:
    """Accept a batch of browser console/error entries and append them to
    FRONTEND_LOG_FILE. Unauthenticated and best-effort: always succeeds,
    even when file writing is disabled (errors happen before any auth
    exists)."""
    service.append_frontend_logs(batch)
    return ok(None)
