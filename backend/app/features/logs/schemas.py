from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FrontendLogEntry(BaseModel):
    ts: datetime
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    source: str = Field(default="app", max_length=50)
    message: str = Field(max_length=4000)


class FrontendLogBatch(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    entries: list[FrontendLogEntry] = Field(max_length=500)
