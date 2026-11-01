from pydantic import BaseModel, Field


class TranscribeResponse(BaseModel):
    text: str


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class SynthesizeResponse(BaseModel):
    """URL path (e.g. /media/files/<uuid>.mp3) the client can fetch or play."""

    url: str
