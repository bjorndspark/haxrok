"""Facts read from each IC's datasheet by the model, for the review to cite.

One model call per datasheet (``prompt.DATASHEET_FACTS``) copies out the
pins, limits and typical application circuit, each with its page. What
comes back is checked before anything uses it: a fact on a page that wasn't
sent, or about a pin the board's part doesn't have, is dropped.

Pin facts replace the hand-written pin table's lines for that IC
(``pins.lookup``) and are cited as ``pin_function``. Limits and application
parts are numbered ``ds1``, ``ds2``, … across the board and cited as kind
``datasheet``.

Answers are cached per part (the PDF's key) and per everything that shapes
the answer (prompt, schema, board pins, model), so a part is read once, not
per run. A datasheet that can't be read (no text layer, a broken file, the
model failing) leaves its IC on the hand-written table; the review goes on.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.core.ai import cache as ai_cache
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.features.debug import pdf, pins, prompt
from app.features.debug.design import Design, Part
from app.features.debug.schemas import AppPart, DatasheetFacts, DatasheetStatus

logger = get_logger(__name__)

Extractor = Callable[[str, str], Awaitable[DatasheetFacts]]
"""(system prompt, user message) -> the facts the model read."""

PdfReader = Callable[[Path], list[pdf.Page]]
"""A PDF's pages to send; ``pdf.read`` outside tests."""

# Concurrent model calls for one board: enough that a first run of a typical
# board reads all its datasheets at once, few enough not to flood the gateway.
_PARALLEL = 4


class FactsCache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str) -> None: ...


class RedisFactsCache:
    """The AI answer cache (Redis, ``AI_CACHE_TTL_S``, fails open)."""

    async def get(self, key: str) -> str | None:
        return await ai_cache.get(key)

    async def put(self, key: str, value: str) -> None:
        await ai_cache.put(key, value)


@dataclass(frozen=True)
class Fact:
    """A limit or typical-application part, numbered for citing."""

    id: str  # "ds3"
    ref: str
    text: str  # "p.44 VDD supply voltage: min 2.4, max 3.6 V"


@dataclass(frozen=True)
class DatasheetReading:
    """What the board's datasheets said, ready for the prompt and the
    evidence check."""

    # ref -> pin number -> (datasheet name, "function (p.N)"), for pins.lookup
    pins: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    # ref -> its typical application circuit, for application.check
    application: dict[str, list[AppPart]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # ref -> PDF URL
    failed: dict[str, str] = field(default_factory=dict)  # ref -> why

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(f.id for f in self.facts)

    @property
    def text(self) -> str:
        """The prompt's "Datasheet facts" section; "" when there are none."""
        lines: list[str] = []
        for ref in self.sources:
            mine = [f for f in self.facts if f.ref == ref]
            if mine:
                lines.append(f"{ref}, from {self.sources[ref]}:")
                lines += [f"  {f.id} {f.text}" for f in mine]
        return "\n".join(lines) + "\n" if lines else ""


class FactReader:
    """Reads every found datasheet of a board; see the module docstring."""

    def __init__(
        self,
        extract: Extractor,
        cache: FactsCache,
        read_pdf: PdfReader = pdf.read,
        timeout_s: float | None = None,
    ) -> None:
        self.extract = extract
        self.cache = cache
        self.read_pdf = read_pdf
        self.timeout_s = timeout_s or settings.DEBUG_AI_TIMEOUT_S
        self._slots = asyncio.Semaphore(_PARALLEL)

    async def read(self, design: Design, sheets: Sequence[DatasheetStatus]) -> DatasheetReading:
        """Facts for every IC whose datasheet was found, in parallel. ICs
        that share a datasheet and pin names (two of the same LDO) share
        one read."""
        tasks: dict[str, asyncio.Task[DatasheetFacts | str]] = {}
        jobs: list[tuple[DatasheetStatus, str]] = []
        for sheet in sheets:
            if sheet.status != "found" or not sheet.path or sheet.ref not in design.parts:
                continue
            path = Path(sheet.path)
            board_pins = _board_pins(design, sheet.ref)
            key = _cache_key(path, design.parts[sheet.ref], board_pins)
            if key not in tasks:
                tasks[key] = asyncio.create_task(
                    self._one(path, design, sheet.ref, board_pins, key)
                )
            jobs.append((sheet, key))
        reading = DatasheetReading()
        n = 0
        for sheet, key in jobs:
            got = await tasks[key]
            if isinstance(got, str):
                reading.failed[sheet.ref] = got
                logger.info("datasheet facts %s (%s): %s", sheet.ref, sheet.value, got)
                continue
            reading.sources[sheet.ref] = sheet.url or "its datasheet"
            if got.pins:
                reading.pins[sheet.ref] = {
                    p.pin: (p.name, f"{p.function} (p.{p.page})") for p in got.pins
                }
            if got.application:
                reading.application[sheet.ref] = got.application
            for line in _fact_lines(got):
                n += 1
                reading.facts.append(Fact(f"ds{n}", sheet.ref, line))
            logger.info(
                "datasheet facts %s (%s): %d pins, %d limits, %d application parts",
                sheet.ref,
                sheet.value,
                len(got.pins),
                len(got.limits),
                len(got.application),
            )
        return reading

    async def _one(
        self, path: Path, design: Design, ref: str, board_pins: list[tuple[str, str]], key: str
    ) -> DatasheetFacts | str:
        """The checked facts for one datasheet, or why there are none."""
        if hit := await self.cache.get(key):
            try:
                return DatasheetFacts.model_validate_json(hit)
            except ValidationError:
                logger.warning("datasheet facts cache entry %s didn't parse; reading again", key)
        try:
            pages = await asyncio.to_thread(self.read_pdf, path)
        except Exception as e:  # noqa: BLE001 - any parser error on a downloaded file is "unreadable"
            return f"the PDF couldn't be read ({type(e).__name__})"
        if not pages:
            return "the PDF has no text (a scan?)"
        part = design.parts[ref]
        user = prompt.build_datasheet_message(
            value=part.value,
            footprint=part.footprint,
            pins=board_pins,
            pages=[(p.number, p.text) for p in pages],
        )
        try:
            async with self._slots, asyncio.timeout(self.timeout_s):
                raw = await self.extract(prompt.DATASHEET_FACTS, user)
        except AppError as e:
            return f"the model couldn't read it: {e.detail}"
        except TimeoutError:
            return f"reading it timed out after {self.timeout_s:g} s"
        facts = check(raw, {p.number for p in pages}, {number for number, _ in board_pins})
        await self.cache.put(key, facts.model_dump_json())
        return facts


def check(facts: DatasheetFacts, pages: set[int], board_pins: set[str]) -> DatasheetFacts:
    """Drop what the model couldn't have read: facts on pages it wasn't
    sent, pins the board's part doesn't have (and duplicates of a pin),
    application pins likewise."""
    seen: set[str] = set()
    kept = []
    for p in facts.pins:
        if p.page in pages and p.pin in board_pins and p.pin not in seen:
            seen.add(p.pin)
            kept.append(p)
    limits = [lim for lim in facts.limits if lim.page in pages]
    application = [
        a.model_copy(
            update={"pins": [pin for pin in a.pins if pin in board_pins]}
            | ({} if a.range_page in pages else _NO_RANGE)
        )
        for a in facts.application
        if a.page in pages
    ]
    dropped = (
        len(facts.pins)
        - len(kept)
        + len(facts.limits)
        - len(limits)
        + len(facts.application)
        - len(application)
    )
    if dropped:
        logger.info("datasheet facts: dropped %d not backed by a sent page or board pin", dropped)
    return DatasheetFacts(pins=kept, limits=limits, application=application)


# A range printed on a page the model wasn't sent isn't one it read.
_NO_RANGE = {"min": None, "max": None, "unit": "", "range_page": None}


def _fact_lines(facts: DatasheetFacts) -> list[str]:
    lines = []
    for lim in facts.limits:
        bounds = [
            f"{k} {_num(v)}"
            for k, v in (("min", lim.min), ("typ", lim.typ), ("max", lim.max))
            if v is not None
        ]
        cond = f" ({lim.conditions})" if lim.conditions else ""
        lines.append(f"p.{lim.page} {lim.parameter}: {', '.join(bounds) or '?'} {lim.unit}{cond}")
    for a in facts.application:
        on = f", on pins {', '.join(a.pins)}" if a.pins else ""
        value = f" {a.value}" if a.value else ""
        bounds = [f"{k} {_num(v)}" for k, v in (("min", a.min), ("max", a.max)) if v is not None]
        allowed = f", allowed {', '.join(bounds)} {a.unit} (p.{a.range_page})" if bounds else ""
        optional = "" if a.required else ", optional"
        lines.append(
            f"p.{a.page} typical application: {a.role}, {a.kind}{value}{on}{allowed}{optional}"
        )
    return lines


def _num(v: float) -> str:
    return f"{v:g}"


def _board_pins(design: Design, ref: str) -> list[tuple[str, str]]:
    """The part's pins on the board as (number, schematic name), in pin
    order (numbers first, then BGA-style names)."""
    numbers = {n.pin for nodes in design.nets.values() for n in nodes if n.ref == ref}
    return [(p, design.pin_name(f"{ref}.{p}")) for p in sorted(numbers, key=pins.pin_order)]


def _cache_key(path: Path, part: Part, board_pins: list[tuple[str, str]]) -> str:
    """Per datasheet (its stored file), and per everything that shapes the
    answer, so a prompt, schema or model change reads it again."""
    shape = json.dumps(
        {
            "value": part.value,
            "footprint": part.footprint,
            "pins": board_pins,
            "system": prompt.DATASHEET_FACTS,
            "schema": DatasheetFacts.model_json_schema(),
            "model": settings.AI_CHAT_MODEL,
            "budget": pdf.BUDGET_CHARS,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(shape.encode()).hexdigest()[:16]
    return f"datasheet:facts:{path.stem}:{digest}"
