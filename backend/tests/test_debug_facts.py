"""Datasheet reading tests: page choice, PDF text, the fact reader's checks,
cache and failures, and how its facts reach the pin table, the prompt and
the evidence checker. The model is a fake extractor; no network."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core.errors import UpstreamError
from app.features.debug import facts, pdf, pins, prompt
from app.features.debug.design import Design, Node, Part
from app.features.debug.evidence import EvidenceContext, check_claim
from app.features.debug.facts import FactReader
from app.features.debug.schemas import (
    AppPart,
    BootLevel,
    Cite,
    Claim,
    DatasheetFacts,
    DatasheetStatus,
    LimitFact,
    LimitKind,
    PinFact,
    PinRuleFact,
    PinRuleKind,
)

# --- pdf: choosing pages ------------------------------------------------------------


def test_choose_sends_every_page_with_text_when_it_fits():
    assert pdf.choose(["Features", "", "1 Pin description\nVDD"], budget=1000) == [1, 3]


def test_choose_prefers_core_headings_and_drops_unwanted_pages():
    filler = "x" * 50
    plain = [
        "Features\n" + filler,  # p.1 always leads
        "1 Introduction\n" + filler,
        "Revision history\n" + filler,
        "4 Pin descriptions\n" + filler,
        "Table 9. Pin definitions (continued)\n" + filler,
        "6 Electrical characteristics\n" + filler,
        "7 Package information\n" + filler,
    ]
    budget = sum(len(t) for t in plain[:2]) + len(plain[3]) * 3  # four pages' worth
    assert pdf.choose(plain, budget) == [1, 4, 5, 6]


def test_continued_page_ranks_with_the_page_it_continues():
    plain = ["Features", "3 Pinout", "lots of rows (continued)", "1 Introduction"]
    assert pdf._ranked(plain)[:3] == [1, 2, 3]


def test_heading_only_scoring_ignores_reset_columns_in_register_tables():
    # A register table's "Reset" column on a long prose line isn't a section.
    register = "Bits 31:0 Reset value 0x0000 " + "y" * 100
    assert pdf._score(register) == 0
    assert pdf._score("2.4 Reset and boot") == 3


def _tiny_pdf(pages: list[str]) -> bytes:
    """A valid PDF, one Helvetica line per page ("" = an empty page)."""
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    font = 3 + 2 * n
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_read_returns_numbered_pages_and_skips_empty_ones(tmp_path: Path):
    path = tmp_path / "sheet.pdf"
    path.write_bytes(_tiny_pdf(["BOOT0 selects the boot mode", "", "VDD 2.4 to 3.6 V"]))
    pages = pdf.read(path)
    assert [p.number for p in pages] == [1, 3]
    assert "BOOT0" in pages[0].text and "3.6 V" in pages[1].text


def test_read_of_a_scan_is_empty(tmp_path: Path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(_tiny_pdf(["", ""]))
    assert pdf.read(path) == []


def test_reads_from_several_threads_at_once_take_turns(tmp_path: Path):
    # PDFium crashes the process (segfault) on concurrent use; FactReader
    # reads a board's datasheets from worker threads in parallel.
    paths = []
    for i in range(8):
        path = tmp_path / f"sheet{i}.pdf"
        path.write_bytes(_tiny_pdf([f"PIN{i} page {n}" for n in range(1, 4)]))
        paths.append(path)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(pdf.read, paths * 8))
    assert all(len(pages) == 3 for pages in results)


# --- the fact reader -------------------------------------------------------------------


def _design() -> Design:
    """An MCU (U1) with BOOT0 and VDD wired, and two of the same LDO."""
    parts = {
        "U1": Part("U1", "STM32F030C8T6", "", "LQFP-48"),
        "U3": Part("U3", "RT9013-33", "", "SOT-23-5"),
        "U4": Part("U4", "RT9013-33", "", "SOT-23-5"),
    }
    nets = {
        "+3V3": [Node("U1", "1", "VDD"), Node("U3", "5", "VOUT"), Node("U4", "5", "VOUT")],
        "GND": [Node("U1", "47", "VSS"), Node("U3", "2", "GND"), Node("U4", "2", "GND")],
        "/BOOT0": [Node("U1", "44", "BOOT0")],
    }
    return Design(parts, nets)


def _sheet(ref: str, value: str, name: str) -> DatasheetStatus:
    return DatasheetStatus(
        ref=ref,
        value=value,
        status="found",
        url=f"https://example.com/{name}.pdf",
        path=f"/app/data/datasheets/{name}.pdf",
    )


def _limit(
    parameter: str,
    lo: float | None,
    hi: float | None,
    page: int,
    kind: LimitKind = "recommended",
    pins: tuple[str, ...] = (),
) -> LimitFact:
    return LimitFact(
        parameter=parameter,
        kind=kind,
        pins=list(pins),
        all_pins=False,
        min=lo,
        typ=None,
        max=hi,
        unit="V",
        conditions="",
        page=page,
    )


MCU_FACTS = DatasheetFacts(
    pins=[
        PinFact(
            pin="44", name="BOOT0", function="Selects the boot memory.", role="signal", page=33
        ),
        PinFact(pin="44", name="BOOT0", function="A duplicate.", role="signal", page=33),
        # not sent
        PinFact(pin="1", name="VDD", function="Digital supply.", role="supply_in", page=99),
        PinFact(pin="12", name="PA2", function="GPIO.", role="signal", page=33),  # not on the board
    ],
    limits=[
        _limit("VDD supply voltage", 2.4, 3.6, 44, pins=("1", "48")),  # 48 isn't wired
        _limit("VBAT supply voltage", 1.65, 3.6, 98),  # not sent
    ],
    application=[
        AppPart(
            role="decoupling capacitor",
            kind="capacitor",
            value="100 nF",
            pins=["1", "48"],  # 48 isn't wired on this board
            page=44,
            min=None,
            max=None,
            unit="",
            range_page=None,
            required=True,
        ),
    ],
    pin_rules=[],
)

VDDA = AppPart(
    role="VDDA filter capacitor",
    kind="capacitor",
    value="1 µF",
    pins=["9"],
    page=44,
    min=0.1,
    max=None,
    unit="µF",
    range_page=99,
    required=False,
)

LDO_FACTS = DatasheetFacts(
    pins=[PinFact(pin="5", name="VOUT", function="Regulated output.", role="supply_out", page=2)],
    limits=[_limit("Input voltage", 2.2, 5.5, 3)],
    application=[],
    pin_rules=[],
)


class FakeExtractor:
    """Answers by part from canned facts, recording each call."""

    def __init__(self, by_value: dict[str, DatasheetFacts | Exception]) -> None:
        self.by_value = by_value
        self.calls: list[str] = []

    async def __call__(self, system: str, user: str) -> DatasheetFacts:
        assert system == prompt.DATASHEET_FACTS
        value = user.splitlines()[0].removeprefix("Part: ")
        self.calls.append(value)
        answer = self.by_value[value]
        if isinstance(answer, Exception):
            raise answer
        return answer


class DictCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def put(self, key: str, value: str) -> None:
        self.data[key] = value


def _pages(path: Path) -> list[pdf.Page]:
    if "stm32" in path.name:
        return [pdf.Page(1, "Features"), pdf.Page(33, "Pinout"), pdf.Page(44, "Limits")]
    return [pdf.Page(n, f"page {n}") for n in (1, 2, 3)]


SHEETS = [
    _sheet("U1", "STM32F030C8T6", "stm32f030"),
    _sheet("U3", "RT9013-33", "rt9013"),
    _sheet("U4", "RT9013-33", "rt9013"),
]


async def test_reader_keeps_only_facts_on_sent_pages_and_board_pins():
    extract = FakeExtractor({"STM32F030C8T6": MCU_FACTS, "RT9013-33": LDO_FACTS})
    reading = await FactReader(extract, DictCache(), _pages).read(_design(), SHEETS)

    assert reading.pins["U1"] == {"44": ("BOOT0", "Selects the boot memory. (p.33)")}
    assert [(f.id, f.ref) for f in reading.facts] == [
        ("ds1", "U1"),
        ("ds2", "U1"),
        ("ds3", "U3"),
        ("ds4", "U4"),
    ]
    assert reading.facts[0].text == (
        "p.44 recommended operating: VDD supply voltage: min 2.4, max 3.6 V, pins 1"
    )
    assert reading.facts[1].text == (
        "p.44 typical application: decoupling capacitor, capacitor 100 nF, on pins 1"
    )
    assert reading.ids == {"ds1", "ds2", "ds3", "ds4"}
    assert [a.role for a in reading.application["U1"]] == ["decoupling capacitor"]
    assert reading.text.startswith(
        "U1, from https://example.com/stm32f030.pdf:\n  ds1 p.44 recommended operating: VDD"
    )
    assert not reading.failed


async def test_two_of_the_same_part_share_one_read_and_a_cache_hit_reads_none():
    cache = DictCache()
    extract = FakeExtractor({"STM32F030C8T6": MCU_FACTS, "RT9013-33": LDO_FACTS})
    await FactReader(extract, cache, _pages).read(_design(), SHEETS)
    assert sorted(extract.calls) == ["RT9013-33", "STM32F030C8T6"]
    assert len(cache.data) == 2

    again = FakeExtractor({})
    reading = await FactReader(again, cache, _pages).read(_design(), SHEETS)
    assert again.calls == []
    assert set(reading.pins) == {"U1", "U3", "U4"}


async def test_a_corrupt_cache_entry_is_read_again():
    cache = DictCache()
    extract = FakeExtractor({"STM32F030C8T6": MCU_FACTS})
    reader = FactReader(extract, cache, _pages)
    await reader.read(_design(), SHEETS[:1])
    cache.data = dict.fromkeys(cache.data, "{not json")
    await reader.read(_design(), SHEETS[:1])
    assert extract.calls == ["STM32F030C8T6", "STM32F030C8T6"]


async def test_failures_leave_the_ic_out_and_the_others_read():
    def pages(path: Path) -> list[pdf.Page]:
        if "rt9013" in path.name:
            return []
        return _pages(path)

    extract = FakeExtractor({"STM32F030C8T6": UpstreamError("gateway down")})
    reading = await FactReader(extract, DictCache(), pages).read(_design(), SHEETS)
    assert reading.failed["U1"] == "the model couldn't read it: gateway down"
    assert reading.failed["U3"] == reading.failed["U4"] == "the PDF has no text (a scan?)"
    assert reading.pins == {} and reading.facts == [] and reading.text == ""


async def test_a_broken_pdf_is_a_failure_not_an_error():
    def broken(path: Path) -> list[pdf.Page]:
        raise ValueError("bad xref")

    reading = await FactReader(FakeExtractor({}), DictCache(), broken).read(_design(), SHEETS)
    assert reading.failed["U1"] == "the PDF couldn't be read (ValueError)"


async def test_sheets_not_found_or_off_the_board_are_not_read():
    sheets = [
        DatasheetStatus(ref="U1", value="STM32F030C8T6", status="not_found"),
        _sheet("U9", "LM358", "lm358"),
    ]
    extract = FakeExtractor({})
    reading = await FactReader(extract, DictCache(), _pages).read(_design(), sheets)
    assert extract.calls == [] and reading.failed == {}


def test_check_filters_application_pins_to_the_board():
    checked = facts.check(MCU_FACTS, {33, 44}, {"1", "44", "47"})
    assert [p.pin for p in checked.pins] == ["44"]
    assert checked.application[0].pins == ["1"]
    [vdd] = checked.limits
    assert vdd.pins == ["1"]


def test_check_keeps_a_part_but_drops_a_range_read_from_a_page_not_sent():
    with_vdda = DatasheetFacts(pins=[], limits=[], application=[VDDA], pin_rules=[])
    [kept] = facts.check(with_vdda, {44, 45}, {"9"}).application
    assert (kept.role, kept.min, kept.unit, kept.range_page) == (
        "VDDA filter capacitor",
        None,
        "",
        None,
    )
    [ranged] = facts.check(with_vdda, {44, 99}, {"9"}).application
    assert (ranged.min, ranged.unit, ranged.range_page) == (0.1, "µF", 99)


def _pin_rule(
    pins: list[str],
    page: int,
    kind: PinRuleKind = "strapping",
    level: BootLevel = "high",
    condition: str = "",
    **timing: object,
) -> PinRuleFact:
    """A pin rule; ``timing`` sets its RC numbers (default null)."""
    fields: dict[str, object] = {
        "internal_pull": None,
        "pull_kohms": None,
        "threshold": None,
        "hold_ms": None,
    }
    return PinRuleFact.model_validate(
        {"kind": kind, "pins": pins, "boot_level": level, "condition": condition}
        | {"text": "Boot mode.", "page": page}
        | fields
        | timing
    )


def test_check_keeps_pin_rules_on_sent_pages_and_board_pins_only():
    rules = [
        _pin_rule(["44", "48"], 33),  # 48 isn't on the board
        _pin_rule(["48"], 33),  # no pin left on the board
        _pin_rule(["44"], 99),  # page not sent
    ]
    read = DatasheetFacts(pins=[], limits=[], application=[], pin_rules=rules)
    [kept] = facts.check(read, {33}, {"44"}).pin_rules
    assert kept.pins == ["44"] and kept.page == 33


def test_check_drops_rc_numbers_no_datasheet_prints():
    rules = [
        _pin_rule(["44"], 33, internal_pull="high", pull_kohms=45, threshold=0.75, hold_ms=3),
        _pin_rule(["44"], 33, internal_pull="high", pull_kohms=0, threshold=2.0, hold_ms=-1),
    ]
    read = DatasheetFacts(pins=[], limits=[], application=[], pin_rules=rules)
    sane, insane = facts.check(read, {33}, {"44"}).pin_rules
    assert (sane.pull_kohms, sane.threshold, sane.hold_ms) == (45, 0.75, 3)
    assert (insane.internal_pull, insane.pull_kohms, insane.threshold, insane.hold_ms) == (
        "high",
        None,
        None,
        None,
    )


def test_fact_line_gives_a_strapping_pins_rc_numbers():
    rules = [
        _pin_rule(["27"], 9, internal_pull="high", pull_kohms=45, threshold=0.75, hold_ms=3),
        _pin_rule(["26"], 9, level="low", internal_pull="low"),
    ]
    read = DatasheetFacts(pins=[], limits=[], application=[], pin_rules=rules)
    assert [line for _, line in facts._fact_lines(read)] == [
        "pin rule: strapping, pins 27, normal boot needs it high, internal pull-up 45 kΩ, "
        "threshold 0.75 × VDD, hold time 3 ms: Boot mode.",
        "pin rule: strapping, pins 26, normal boot needs it low, internal pull-down: Boot mode.",
    ]


def test_fact_line_names_a_pin_rule_with_its_boot_level_and_condition():
    rules = [
        _pin_rule(["27"], 9),
        _pin_rule(["28", "29"], 4, kind="reserved", level="either", condition="with octal PSRAM"),
    ]
    read = DatasheetFacts(pins=[], limits=[], application=[], pin_rules=rules)
    assert facts._fact_lines(read) == [
        (9, "pin rule: strapping, pins 27, normal boot needs it high: Boot mode."),
        (4, "pin rule: reserved, pins 28, 29, with octal PSRAM: Boot mode."),
    ]


def test_fact_line_shows_the_allowed_range_and_optional_parts():
    vdda = VDDA.model_copy(update={"max": 22})
    lines = facts._fact_lines(DatasheetFacts(pins=[], limits=[], application=[vdda], pin_rules=[]))
    assert lines == [
        (
            44,
            "typical application: VDDA filter capacitor, capacitor 1 µF, on pins 9, "
            "allowed min 0.1, max 22 µF (p.99), optional",
        )
    ]


# --- where the facts go --------------------------------------------------------------


def _buck_boost() -> Design:
    parts = {"U2": Part("U2", "TPS63070RNMR", "", "VQFN-15"), "L1": Part("L1", "1u5", "", "")}
    nets = {
        "+3V3": [Node("U2", "7", "VOUT"), Node("U2", "9", "L2"), Node("L1", "2", "")],
        "/SW1": [Node("U2", "11", "L1"), Node("L1", "1", "")],
    }
    return Design(parts, nets)


def test_datasheet_pins_replace_the_hand_written_lines_but_wiring_checks_stay():
    design = _buck_boost()
    table = pins.lookup(design)
    read = {"U2": {"9": ("L2", "Output-side switch node. (p.4)")}}
    lookup = pins.lookup(design, datasheet_pins=read)

    assert "U2 (TPS63070RNMR), from its datasheet:" in lookup.text
    assert "U2.9 L2: Output-side switch node. (p.4)" in lookup.text
    assert "U2.7" not in lookup.text  # only what the datasheet read covers
    assert lookup.pin_refs == {"U2.9"}
    assert [f.pin for f in lookup.findings] == [f.pin for f in table.findings] == ["U2.9"]


def test_pin_order_puts_numbers_first_then_ball_names():
    assert sorted(["10", "A2", "2", "B1", "1"], key=pins.pin_order) == ["1", "2", "10", "A2", "B1"]


def test_datasheet_facts_section_only_when_there_are_some():
    common = {
        "symptoms": [],
        "measurements": [],
        "netlist": "n",
        "erc": "e",
        "pin_table": "p",
        "findings": "f",
    }
    assert "## Datasheet facts" not in prompt.build_user_message(**common)
    with_facts = prompt.build_user_message(**common, datasheet_facts="U1, from x:\n  ds1 p.44 …\n")
    assert "## Datasheet facts" in with_facts and "ds1 p.44" in with_facts


@pytest.mark.parametrize(("cite_id", "kept"), [("ds1", True), ("ds9", False), (None, False)])
def test_a_datasheet_cite_needs_a_fact_that_was_read(cite_id: str | None, kept: bool):
    ctx = EvidenceContext(
        design=_buck_boost(),
        erc={},
        pin_data=frozenset(),
        symptom_ids=frozenset(),
        measurement_ids=frozenset(),
        datasheet_ids=frozenset({"ds1", "ds2"}),
    )
    claim = Claim(
        text="VDD may be at most 3.6 V.",
        evidence="datasheet",
        cites=[Cite(kind="datasheet", pin=None, net=None, id=cite_id)],
    )
    checked = check_claim(claim, ctx)
    assert (checked.evidence == "datasheet") is kept
    assert checked.downgraded is not kept
