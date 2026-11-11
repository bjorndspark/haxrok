"""Debug routes. Thin: read the multipart upload, hand it to the service,
stream the result as SSE (exempt from the envelope, see app.core.responses);
answer questions about a simulation or a finished run, and re-run an RC
simulation with other values, in the envelope."""

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from app.core.ai.chat import complete_text
from app.core.ai.structured import complete_structured
from app.core.config import settings
from app.core.errors import BadRequestError
from app.core.responses import SuccessResponse, ok
from app.features.debug import power, service
from app.features.debug.datasheet import Datasheets, DatasheetStore, HttpFetcher
from app.features.debug.facts import FactReader, RedisFactsCache
from app.features.debug.kicad import KicadCli
from app.features.debug.schemas import (
    Analysis,
    AskAnswer,
    AskRequest,
    Claim,
    DatasheetFacts,
    Language,
    MeasurementInput,
    Mode,
    RerunRequest,
    ResultAskRequest,
    SimAnalysis,
    Simulation,
)
from app.features.debug.spice import Ngspice
from app.features.debug.spice_cache import CachedRunner
from app.features.debug.spice_models import SpiceModels, SpiceModelStore

router = APIRouter(prefix="/debug", tags=["debug"])

_measurements = TypeAdapter(list[MeasurementInput])
_analyses = TypeAdapter(list[SimAnalysis])


def parse_analyses(text: str) -> list[SimAnalysis]:
    """The ``analyses`` form field: comma-separated names, "" for none."""
    names = [a.strip() for a in text.split(",") if a.strip()]
    try:
        return list(dict.fromkeys(_analyses.validate_python(names)))
    except ValidationError as e:
        raise BadRequestError(
            "analyses must be startup, load_step or dropout, comma-separated.", field="analyses"
        ) from e


async def _diagnose(system: str, user: str) -> str:
    return await complete_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        name="debug_diagnosis",
        timeout_s=settings.DEBUG_AI_TIMEOUT_S,
    )


async def _structure(system: str, user: str) -> Analysis[Claim]:
    return await complete_structured(
        Analysis[Claim],
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        name="debug_analysis",
        timeout_s=settings.DEBUG_AI_TIMEOUT_S,
    )


async def _extract(system: str, user: str) -> DatasheetFacts:
    return await complete_structured(
        DatasheetFacts,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        name="datasheet_facts",
        timeout_s=settings.DEBUG_AI_TIMEOUT_S,
    )


async def _answer(system: str, user: str) -> str:
    return await complete_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        name="debug_ask",
        timeout_s=settings.DEBUG_AI_TIMEOUT_S,
    )


@router.post("/analyze")
async def analyze(
    files: Annotated[
        list[UploadFile],
        File(description="The project's .kicad_pro and .kicad_sch files, and SPICE model files"),
    ],
    symptoms: Annotated[
        str, Form(max_length=8000, description="Bench symptoms; review: optional board notes")
    ] = "",
    measurements: Annotated[str, Form(description="JSON list of MeasurementInput")] = "[]",
    mode: Annotated[Mode, Form(description="debug (bench symptoms) or review (pre-fab)")] = "debug",
    language: Annotated[
        Language, Form(description="Language of the written-up answer: en or ja")
    ] = "en",
    analyses: Annotated[
        str,
        Form(
            description="Vendor-model simulations, comma-separated: startup, load_step, "
            "dropout; empty for none"
        ),
    ] = "startup",
    battery: Annotated[
        str,
        Form(
            max_length=40,
            description='The battery\'s voltage, "3.7" or a range "3.0-4.2"; empty to infer it',
        ),
    ] = "",
) -> EventSourceResponse:
    """Analyze a KiCad project against bench symptoms, or (``mode=review``)
    review it for design mistakes before the board is ordered.

    Upload errors, missing sub-sheets and kicad-cli failures return the
    error envelope. Otherwise the response is an SSE stream:
    ``design_summary``, ``progress`` and ``simulation`` (Simulation, as each
    ngspice run finishes)…, then ``result`` (CheckedAnalysis) or ``error``,
    then a ``simulation`` for each vendor-model run in ``analyses`` (minutes
    each). File names carry the path inside the dropped folder.
    """
    try:
        parsed = _measurements.validate_python(json.loads(measurements))
    except (json.JSONDecodeError, ValidationError) as e:
        raise BadRequestError("measurements must be a JSON list.", field="measurements") from e
    picked = parse_analyses(analyses)
    try:
        cell = power.parse_battery(battery)
    except ValueError as e:
        raise BadRequestError(
            'battery must be a voltage, "3.7", or a range, "3.0-4.2", within 0-60 V.',
            field="battery",
        ) from e
    if len(files) > settings.DEBUG_MAX_FILES:
        raise BadRequestError(f"Too many files; at most {settings.DEBUG_MAX_FILES}.")

    raw: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > settings.DEBUG_MAX_UPLOAD_BYTES:
            raise BadRequestError(
                f"The schematics add up to more than {settings.DEBUG_MAX_UPLOAD_BYTES // 2**20} MB."
            )
        raw.append((f.filename or "", data))

    fetcher = HttpFetcher()
    datasheets = Datasheets(fetcher, DatasheetStore(Path(settings.DATASHEET_DIR)))
    models = SpiceModels(fetcher, SpiceModelStore(Path(settings.SPICE_MODEL_DIR)))
    ngspice = Ngspice()
    prepared = await service.prepare(
        raw,
        symptoms,
        parsed,
        KicadCli(),
        datasheets,
        models,
        mode,
        language,
        ngspice=ngspice,
        analyses=picked,
        battery=cell,
    )
    return EventSourceResponse(
        service.stream(
            prepared,
            service.Model(diagnose=_diagnose, structure=_structure),
            ngspice,
            FactReader(_extract, RedisFactsCache()),
            CachedRunner(Ngspice(timeout_s=settings.NGSPICE_MODEL_TIMEOUT_S)),
        )
    )


@router.post("/ask")
async def ask(req: AskRequest) -> SuccessResponse[AskAnswer]:
    """Answer a question about one simulation from an analysis stream. The
    browser sends the simulation, its latest what-if and the conversation
    so far each time; nothing is stored."""
    return ok(AskAnswer(answer=await service.ask(req, _answer)))


@router.post("/rerun")
async def rerun(req: RerunRequest) -> SuccessResponse[Simulation]:
    """Re-run an RC simulation with another R and C (a what-if) and return
    the new waveform; no model call. The browser sends the original
    simulation back; nothing is stored. Values outside 1 Ω–100 MΩ or
    0.1 pF–0.1 F are a 400."""
    return ok(await service.rerun(req, Ngspice()))


@router.post("/ask-result")
async def ask_result(req: ResultAskRequest) -> SuccessResponse[AskAnswer]:
    """Answer a follow-up question about a finished run. The browser sends
    the design summary, the checked result and the conversation so far each
    time; nothing is stored."""
    return ok(AskAnswer(answer=await service.ask_result(req, _answer)))
