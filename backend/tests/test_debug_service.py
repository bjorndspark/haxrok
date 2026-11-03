"""Debug feature, end to end below the model: the upload checks, the pin
table, the service's event stream and the /debug/analyze route.

kicad-cli and the model are faked, except in the one test that runs the real
kicad-cli on the demo board (skipped where it isn't installed).
"""

import json
import shutil
from pathlib import Path

import pytest

from app.core.errors import BadRequestError, UpstreamError
from app.features.debug import pins, router, service
from app.features.debug.design import parse_netlist
from app.features.debug.kicad import KicadCli, KicadOutput
from app.features.debug.project import Project, load_project, missing_sheets, normalize_paths
from app.features.debug.prompt import number_symptoms
from app.features.debug.schemas import (
    Analysis,
    Cite,
    Claim,
    Fix,
    Hypothesis,
    MeasurementInput,
)

# tests/../../frontend/public/examples: the demo board the UI offers.
EXAMPLE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "examples" / "rust-board-fb-swapped"

ROOT_SCH = b'(kicad_sch (version 20231120)\n (sheet (property "Sheetfile" "power.kicad_sch"))\n)'
SUB_SCH = b"(kicad_sch (version 20231120))"

NETLIST = """(export (version "E")
  (components
    (comp (ref "U2") (value "TPS63070RNMR") (footprint "VQFN-15")
      (libsource (lib "TPS63070RNMR") (part "TPS63070RNMR") (description "")))
    (comp (ref "U9") (value "LM358") (footprint "SOIC-8")
      (libsource (lib "Amplifier") (part "LM358") (description "")))
    (comp (ref "L1") (value "1u5") (footprint "L_1210")
      (libsource (lib "Device") (part "L") (description ""))))
  (nets
    (net (code "1") (name "+3V3")
      (node (ref "L1") (pin "2") (pintype "passive"))
      (node (ref "U2") (pin "7") (pinfunction "VOUT") (pintype "output"))
      (node (ref "U2") (pin "9") (pinfunction "L2") (pintype "passive")))
    (net (code "2") (name "/Power/SW1")
      (node (ref "L1") (pin "1") (pintype "passive"))
      (node (ref "U2") (pin "11") (pinfunction "L1") (pintype "passive")))))
"""

ERC = json.dumps(
    {
        "sheets": [
            {
                "path": "/Power/",
                "violations": [
                    {
                        "type": "multiple_net_names",
                        "severity": "warning",
                        "description": "Both +3V3 and SW2 are attached to the same items",
                        "items": [{"description": "Label 'SW2'"}],
                    }
                ],
            }
        ]
    }
)


class FakeKicad:
    def __init__(self) -> None:
        self.projects: list[Project] = []

    async def export(self, project: Project) -> KicadOutput:
        self.projects.append(project)
        return KicadOutput(netlist=NETLIST, erc=ERC)


def _files() -> list[tuple[str, bytes]]:
    return [
        ("board/board.kicad_pro", b"{}"),
        ("board/board.kicad_sch", ROOT_SCH),
        ("board/power.kicad_sch", SUB_SCH),
        ("board/board.kicad_pcb", b"never sent, but dropped if it is"),
    ]


def _analysis() -> Analysis[Claim]:
    return Analysis[Claim](
        hypotheses=[
            Hypothesis[Claim](
                id="h1",
                rank=1,
                title="L2 is on the output rail",
                summary="s",
                claims=[
                    Claim(
                        text="U2.9 (L2) is on +3V3.",
                        evidence="schematic",
                        cites=[Cite(kind="pin_on_net", pin="U2.9", net="+3V3", id=None)],
                    ),
                    Claim(
                        text="U2.9 is the output-side inductor connection.",
                        evidence="datasheet",
                        cites=[Cite(kind="pin_function", pin="U2.9", net=None, id=None)],
                    ),
                    Claim(
                        text="It reads 0 ohm.",
                        evidence="measurement",
                        cites=[Cite(kind="measurement", pin=None, net=None, id="m2")],
                    ),
                ],
                explains=["s1"],
                does_not_explain=[],
                confirm=[],
                fix=Fix(schematic=[], rework=[]),
            )
        ],
        open_questions=[],
        glossary=[],
    )


async def _collect(prepared: service.Prepared, model: service.Model) -> list[tuple[str, dict]]:
    return [(e["event"], json.loads(e["data"])) async for e in service.stream(prepared, model)]


async def _diagnose(system: str, user: str) -> str:
    return "1. L2 tied to VOUT."


async def _structure(system: str, user: str) -> Analysis[Claim]:
    return _analysis()


# --- upload -----------------------------------------------------------------------------


def test_load_project_strips_the_folder_and_finds_the_root():
    project = load_project(_files())
    assert project.root == "board.kicad_sch"
    assert set(project.files) == {"board.kicad_pro", "board.kicad_sch", "power.kicad_sch"}
    assert project.name == "board"


def test_missing_sub_sheet_is_named():
    files = [f for f in _files() if "power" not in f[0]]
    with pytest.raises(BadRequestError, match="power.kicad_sch"):
        load_project(files)


def test_single_sheet_without_project_file_is_its_own_root():
    assert load_project([("flat.kicad_sch", SUB_SCH)]).root == "flat.kicad_sch"


def test_several_sheets_without_project_file_is_refused():
    with pytest.raises(BadRequestError, match="root sheet is unknown"):
        load_project([("a.kicad_sch", SUB_SCH), ("b.kicad_sch", SUB_SCH)])


def test_paths_that_escape_the_folder_are_refused():
    with pytest.raises(BadRequestError, match="Unsafe"):
        normalize_paths([("../etc/x.kicad_sch", SUB_SCH)])


def test_sub_sheets_in_subfolders_resolve_relative_to_their_parent():
    root = b'(kicad_sch (sheet (property "Sheetfile" "sub/a.kicad_sch")))'
    nested = b'(kicad_sch (sheet (property "Sheetfile" "b.kicad_sch")))'
    files = {"r.kicad_sch": root, "sub/a.kicad_sch": nested}
    assert missing_sheets(files, "r.kicad_sch") == ["sub/b.kicad_sch"]


def test_symptoms_are_numbered_by_line():
    symptoms = number_symptoms("- Fails on battery\n\n* Works on USB 5 V \n")
    assert [(s.id, s.text) for s in symptoms] == [
        ("s1", "Fails on battery"),
        ("s2", "Works on USB 5 V"),
    ]


# --- pin table ----------------------------------------------------------------------------


def test_pin_lookup_covers_known_ics_and_names_the_rest():
    lookup = pins.lookup(parse_netlist(NETLIST))
    assert [(ic.ref, ic.pin_data) for ic in lookup.ics] == [("U2", True), ("U9", False)]
    assert lookup.pin_refs == {"U2.7", "U2.9", "U2.11"}
    assert "U2.9 L2: Inductor connection" in lookup.text
    assert "No pin table for: U9." in lookup.text


def test_pin_table_needs_the_right_package():
    entry = next(e for e in pins.PIN_TABLE if e.value_prefix == "TPS2121")
    assert entry.matches("TPS2121RUXR", "", "")
    assert not entry.matches("TPS2121YFP", "", "")


# --- service ---------------------------------------------------------------------------------


async def test_stream_sends_summary_progress_then_checked_result():
    kicad = FakeKicad()
    prepared = await service.prepare(
        _files(),
        "Fails on battery\nWorks on USB",
        [MeasurementInput(step_id="h1-c1", context="Ohms L2 to VOUT", value="0.2 ohm")],
        kicad,
    )
    assert kicad.projects[0].root == "board.kicad_sch"
    seen: list[str] = []
    structured: list[str] = []

    async def diagnose(system: str, user: str) -> str:
        seen.append(user)
        return "1. L2 tied to VOUT."

    async def structure(system: str, user: str) -> Analysis[Claim]:
        structured.append(user)
        return _analysis()

    events = await _collect(prepared, service.Model(diagnose=diagnose, structure=structure))
    names = [name for name, _ in events]
    assert names == ["design_summary"] + ["progress"] * 4 + ["result"]
    stages = [data["stage"] for name, data in events if name == "progress"]
    assert stages == ["pin_lookup", "reasoning", "structuring", "checking"]

    summary = events[0][1]
    assert (summary["project"], summary["sheets"], summary["parts"]) == ("board", 2, 3)
    assert summary["erc"][0]["id"] == "erc-1"

    # The model reads numbered symptoms, the measurement with its step, the
    # compact netlist, the ERC with ids and the pin table.
    assert "s1: Fails on battery" in seen[0]
    assert "m1: 0.2 ohm  (for the step: Ohms L2 to VOUT)" in seen[0]
    assert "+3V3: L1.2 U2.7(VOUT) U2.9(L2)" in seen[0]
    assert "erc-1 [multiple_net_names]" in seen[0]
    assert "U2.9 L2:" in seen[0]

    # The structuring call gets the diagnosis, then the same input to cite.
    assert structured[0].startswith("## Diagnosis\n1. L2 tied to VOUT.")
    assert seen[0] in structured[0]

    claims = events[-1][1]["hypotheses"][0]["claims"]
    assert [c["evidence"] for c in claims] == ["schematic", "datasheet", "none"]
    assert claims[2]["downgraded"] and "m2" in claims[2]["reason"]


async def test_model_failure_ends_the_stream_with_an_error_event():
    prepared = await service.prepare(_files(), "Dead board", [], FakeKicad())

    async def failing_diagnose(system: str, user: str) -> str:
        raise UpstreamError("The model call failed: 503")

    async def failing_structure(system: str, user: str) -> Analysis[Claim]:
        raise UpstreamError("The model's answer didn't match the expected format.")

    events = await _collect(prepared, service.Model(failing_diagnose, _structure))
    assert events[-1] == ("error", {"message": "The model call failed: 503"})

    events = await _collect(prepared, service.Model(_diagnose, failing_structure))
    assert events[-1] == (
        "error",
        {"message": "The model's answer didn't match the expected format."},
    )


async def test_blank_symptoms_are_refused_before_kicad_runs():
    kicad = FakeKicad()
    with pytest.raises(BadRequestError, match="bench"):
        await service.prepare(_files(), " \n ", [], kicad)
    assert kicad.projects == []


# --- route ------------------------------------------------------------------------------------


@pytest.fixture
def fake_backends(monkeypatch):
    monkeypatch.setattr(router, "KicadCli", FakeKicad)

    monkeypatch.setattr(router, "_diagnose", _diagnose)
    monkeypatch.setattr(router, "_structure", _structure)


async def test_analyze_streams_sse(client, fake_backends):
    files = [("files", (name, data)) for name, data in _files()]
    res = await client.post("/api/v1/debug/analyze", files=files, data={"symptoms": "Dead"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert "event: design_summary" in res.text
    assert "event: result" in res.text


async def test_analyze_bad_upload_returns_the_envelope(client, fake_backends):
    files = [("files", ("board/board.kicad_pro", b"{}"))]
    res = await client.post("/api/v1/debug/analyze", files=files, data={"symptoms": "Dead"})
    assert res.status_code == 400
    body = res.json()
    assert body["success"] is False
    assert "board.kicad_sch" in body["error"]["message"]


async def test_analyze_rejects_malformed_measurements(client, fake_backends):
    files = [("files", (name, data)) for name, data in _files()]
    res = await client.post(
        "/api/v1/debug/analyze", files=files, data={"symptoms": "x", "measurements": "{"}
    )
    assert res.status_code == 400
    assert res.json()["error"]["field"] == "measurements"


# --- real kicad-cli -------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
@pytest.mark.skipif(not EXAMPLE.is_dir(), reason="demo board not mounted")
async def test_kicad_cli_reads_the_demo_board():
    raw = [(f"demo/{p.name}", p.read_bytes()) for p in sorted(EXAMPLE.glob("*.kicad_*"))]
    prepared = await service.prepare(raw, "Fails on battery", [], KicadCli())
    design = prepared.design
    # The bug the demo is built around: L2 and VOUT on the same net.
    assert design.net_of("U2.9") == design.net_of("U2.7")
    assert any(e.rule == "multiple_net_names" for e in prepared.erc)
    assert not any(e.rule == "lib_symbol_issues" for e in prepared.erc)
    assert {ic.ref for ic in prepared.summary.ics if ic.pin_data} >= {"U1", "U3", "U4", "U2"}
