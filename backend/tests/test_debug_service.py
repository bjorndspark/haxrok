"""Debug feature, end to end below the model: the upload checks, the pin
table, the service's event stream and the /debug/analyze route.

kicad-cli and the model are faked.
"""

import json

import pytest

from app.core.errors import BadRequestError, UpstreamError
from app.features.debug import diagram, pins, prompt, router, service
from app.features.debug.design import Design, Label, Node, Part, parse_labels, parse_netlist
from app.features.debug.evidence import EvidenceContext
from app.features.debug.kicad import KicadOutput
from app.features.debug.project import Project, load_project, missing_sheets, normalize_paths
from app.features.debug.prompt import number_symptoms
from app.features.debug.schemas import (
    Analysis,
    CheckedClaim,
    Cite,
    Claim,
    ErcEntry,
    Fix,
    Hypothesis,
    MeasurementInput,
)

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

# The same power stage wired as intended: L2 on its own net with the
# inductor and a test point, VOUT on +3V3.
FIXED_NETLIST = """(export (version "E")
  (components
    (comp (ref "U2") (value "TPS63070RNMR") (footprint "VQFN-15")
      (libsource (lib "TPS63070RNMR") (part "TPS63070RNMR") (description "")))
    (comp (ref "L1") (value "1u5") (footprint "L_1210")
      (libsource (lib "Device") (part "L") (description ""))))
  (nets
    (net (code "1") (name "+3V3")
      (node (ref "U2") (pin "7") (pinfunction "VOUT") (pintype "output")))
    (net (code "2") (name "/Power/SW1")
      (node (ref "L1") (pin "1") (pintype "passive"))
      (node (ref "U2") (pin "11") (pinfunction "L1") (pintype "passive")))
    (net (code "3") (name "/Power/SW2")
      (node (ref "L1") (pin "2") (pintype "passive"))
      (node (ref "TP9") (pin "1") (pintype "passive"))
      (node (ref "U2") (pin "9") (pinfunction "L2") (pintype "passive")))))
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
                severity=None,
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


def test_inductor_pin_on_the_output_rail_is_a_finding():
    lookup = pins.lookup(parse_netlist(NETLIST))
    [finding] = lookup.findings
    assert (finding.id, finding.pin, finding.pin_name) == ("wf1", "U2.9", "L2")
    assert finding.net == "+3V3"
    assert finding.conflicts == ["U2.7"]
    assert finding.inductors == ["L1.2"]
    assert "U2.7(VOUT)" in finding.text
    assert "wf1: U2.9 (L2) is an inductor connection" in pins.format_findings(lookup.findings)


def test_inductor_pins_wired_to_their_inductor_are_quiet():
    lookup = pins.lookup(parse_netlist(FIXED_NETLIST))
    assert lookup.findings == []
    assert pins.format_findings([]).startswith("(none")


def test_inductor_pin_without_an_inductor_is_a_finding():
    no_inductor = NETLIST.replace('(node (ref "L1") (pin "1") (pintype "passive"))', "")
    findings = pins.lookup(parse_netlist(no_inductor)).findings
    assert [f.pin for f in findings] == ["U2.9", "U2.11"]
    assert "no inductor" in findings[1].text


# A second SW2 label dropped on VOUT: KiCad merges L2 into +3V3 and the
# netlist keeps only the power net's name. The ERC entry names the label.
POWER_SCH = b"""(kicad_sch (version 20231120)
  (label "SW2" (at 116.84 44.45 0) (uuid "aaaa"))
  (label "SW2" (at 116.84 48.26 0) (uuid "bbbb"))
  (global_label "SW2" (at 10 10 0) (uuid "cccc"))
)"""


def _merged(sheet: str, label: str, uuid: str) -> ErcEntry:
    return ErcEntry(
        id="erc-3",
        sheet=sheet,
        rule="multiple_net_names",
        severity="warning",
        message=f"Both +3V3 and {label} are attached to the same items; "
        "+3V3 will be used in the netlist",
        items=["Symbol #PWR35 Pin 1 [Power input, Line]", f"Label '{label}'"],
        uuids=["pwr", uuid],
    )


def _on_sheet(netlist: str, sheet: str) -> Design:
    design = parse_netlist(netlist)
    design.parts["U2"] = Part("U2", "TPS63070RNMR", "", "VQFN-15", sheet)
    return design


def test_parse_labels_reads_every_kind_with_position_and_uuid():
    labels = parse_labels({"power.kicad_sch": POWER_SCH, "board.kicad_pro": b"{}"})
    assert labels[0] == Label("SW2", "label", "power.kicad_sch", 116.84, 44.45, "aaaa")
    assert [(lb.kind, lb.uuid) for lb in labels] == [
        ("label", "aaaa"),
        ("label", "bbbb"),
        ("global_label", "cccc"),
    ]


def test_parse_netlist_keeps_the_sheet_path():
    netlist = NETLIST.replace(
        '(footprint "VQFN-15")', '(footprint "VQFN-15") (sheetpath (names "/Power/"))'
    )
    assert parse_netlist(netlist).parts["U2"].sheet == "/Power/"


def test_a_label_merged_into_the_shorted_net_is_named_as_the_cause():
    labels = parse_labels({"power.kicad_sch": POWER_SCH})
    erc = [_merged("/Power/", "SW2", "bbbb")]
    [finding] = pins.lookup(_on_sheet(NETLIST, "/Power/"), erc, labels).findings
    assert "erc-3 reports the SW2 label at (116.84, 48.26) mm on power.kicad_sch" in finding.text
    # Only the same-kind, same-sheet label joins it; the global one doesn't.
    assert "another SW2 label sits at (116.84, 44.45) mm" in finding.text
    assert "not the wiring" in finding.text


def test_label_causes_skip_other_sheets_and_unrelated_names():
    labels = parse_labels({"power.kicad_sch": POWER_SCH})
    elsewhere = [_merged("/IMU/", "SW2", "bbbb")]
    [finding] = pins.lookup(_on_sheet(NETLIST, "/Power/"), elsewhere, labels).findings
    assert "Likely cause" not in finding.text
    # Same sheet, but a label named after VDD rather than L2 is noise when
    # one named after the pin is there too.
    both = [_merged("/Power/", "IMU_VDD", "x"), _merged("/Power/", "SW_L2", "y")]
    [finding] = pins.lookup(_on_sheet(NETLIST, "/Power/"), both, labels).findings
    assert "SW_L2" in finding.text
    assert "IMU_VDD" not in finding.text


def test_erc_item_uuids_stay_on_the_server():
    assert "uuids" not in _merged("/Power/", "SW2", "bbbb").model_dump()


def test_diagram_shows_the_cited_ic_its_nets_and_the_flagged_pin():
    design = parse_netlist(NETLIST)
    findings = pins.lookup(design).findings
    [h] = [
        Hypothesis[CheckedClaim](
            **h.model_dump(exclude={"claims"}),
            claims=[CheckedClaim(**c.model_dump()) for c in h.claims],
        )
        for h in _analysis().hypotheses
    ]
    d = diagram.build(h, design, findings)
    assert d is not None
    assert (d.ic, d.value) == ("U2", "TPS63070RNMR")
    # U2.7 isn't cited, but it is on the flagged pin's net, so it is shown.
    assert [(p.pin, p.name, p.flagged) for p in d.pins] == [
        ("U2.7", "VOUT", False),
        ("U2.9", "L2", True),
    ]
    [net] = d.nets
    assert net.name == "+3V3"
    assert [(m.pin, m.inductor) for m in net.members] == [("L1.2", True)]
    assert net.more == 0


def test_no_diagram_without_an_ic_pin():
    h = Hypothesis[CheckedClaim](
        id="h2",
        rank=2,
        severity=None,
        title="t",
        summary="s",
        claims=[CheckedClaim(text="The cable has no data wires.", evidence="none", cites=[])],
        explains=[],
        does_not_explain=[],
        confirm=[],
        fix=Fix(schematic=[], rework=[]),
    )
    assert diagram.build(h, parse_netlist(NETLIST), []) is None


def test_pin_table_needs_the_right_package():
    entry = next(e for e in pins.PIN_TABLE if e.value_prefix == "TPS2121")
    assert entry.matches("TPS2121RUXR", "", "")
    assert not entry.matches("TPS2121YFP", "", "")


def _mosfet(
    names: tuple[str, str, str],
    footprint: str = "Package_TO_SOT_SMD:SOT-23",
    value: str = "AO3400A",
) -> Design:
    """A low-side switch whose symbol names pins 1-3 ``names``, wired by
    name: GATE to G, LOAD to D, GND to S."""
    by_name = {"G": "/GATE", "D": "/LOAD", "S": "GND"}
    return Design(
        parts={"Q1": Part("Q1", value, "Simulation_SPICE:NMOS", footprint)},
        nets={
            by_name[name[0]]: [Node("Q1", str(number), name)]
            for number, name in enumerate(names, start=1)
        },
    )


def test_mosfet_symbol_numbered_against_the_part_is_one_finding():
    lookup = pins.lookup(_mosfet(("D", "G", "S")))
    [finding] = lookup.findings
    assert (finding.pin, finding.pin_name, finding.net) == ("Q1.1", "G", "/LOAD")
    assert finding.conflicts == ["Q1.2", "Q1.3"]
    assert "numbered 1=D, 2=G, 3=S, but the AO3400A is 1=G, 2=S, 3=D" in finding.text
    assert "net /GATE (drawn on G) lands on the S pad (Q1.2)" in finding.text
    assert {"Q1.1", "Q1.2", "Q1.3"} <= lookup.pin_refs
    assert lookup.ics == []  # a transistor isn't counted as an IC


def test_mosfet_check_is_quiet_on_a_matching_or_unnamed_symbol_or_other_package():
    assert pins.lookup(_mosfet(("G", "S", "D"))).findings == []
    assert pins.lookup(_mosfet(("GATE", "SOURCE", "DRAIN"))).findings == []
    unnamed = _mosfet(("D", "G", "S"))
    unnamed.nets = {net: [Node(n.ref, n.pin, "")] for net, [n] in unnamed.nets.items()}
    assert pins.lookup(Design(unnamed.parts, unnamed.nets)).findings == []
    assert pins.lookup(_mosfet(("D", "G", "S"), "SOT-23-6")).findings == []
    assert pins.lookup(_mosfet(("D", "G", "S"), "AO3400A:SOT95P280X125-3N")).findings


def test_every_sot23_mosfet_in_the_table_is_checked():
    for value in ("AO3400A", "AO3401A", "2N7002", "BSS138"):
        lookup = pins.lookup(_mosfet(("D", "G", "S"), value=value))
        assert [f.pin for f in lookup.findings] == ["Q1.1"], value
        assert lookup.warnings == [], value


def test_unknown_sot23_mosfet_in_another_order_is_an_unverified_warning():
    lookup = pins.lookup(_mosfet(("D", "G", "S"), value="SI2302"))
    assert lookup.findings == []
    [warning] = lookup.warnings
    assert (warning.id, warning.ref, warning.pins) == ("pw1", "Q1", ["Q1.1", "Q1.2", "Q1.3"])
    assert "net /LOAD (drawn on D) would land on the G pad (Q1.1)" in warning.text
    assert "UNVERIFIED" in warning.text
    assert pins.format_warnings(lookup.warnings).startswith("pw1: Q1 (SI2302)")


def test_convention_warning_skips_the_usual_order_bipolars_and_other_packages():
    assert pins.lookup(_mosfet(("G", "S", "D"), value="SI2302")).warnings == []
    assert pins.lookup(_mosfet(("D", "G", "S"), "SOT-23-6", "SI2302")).warnings == []
    assert pins.lookup(_mosfet(("D", "G", "S"), "", "SI2302")).warnings == []
    bjt = Design(
        parts={"Q1": Part("Q1", "MMBT3904", "Device:Q_NPN_BEC", "SOT-23")},
        nets={f"/N{i}": [Node("Q1", str(i), name)] for i, name in enumerate("BEC", start=1)},
    )
    assert pins.lookup(bjt).warnings == []


def test_pin_warnings_reach_the_model_as_unverified():
    user = prompt.build_user_message(
        symptoms=[],
        measurements=[],
        netlist="",
        erc="",
        pin_table="",
        findings="",
        warnings="pw1: Q1 ...\n",
    )
    assert "## Pin warnings (UNVERIFIED" in user
    assert "pw1: Q1" in user


# --- spice ---------------------------------------------------------------------------------


def _rc_wrdata(netlist: str) -> str:
    """What ngspice's wrdata writes for the deck: v(sda) of an ideal RC."""
    import math

    values = {}
    for row in netlist.splitlines():
        cols = row.split()
        if cols and cols[0] in {"Vpull", "Rpull", "Cbus"}:
            values[cols[0]] = float(cols[3] if cols[0] != "Vpull" else cols[4])
    tau = values["Rpull"] * values["Cbus"]
    return "\n".join(
        f" {t:.6e}  {values['Vpull'] * (1 - math.exp(-t / tau)):.6e}"
        for t in (i * 5 * tau / 200 for i in range(201))
    )


class FakeSpice:
    """Answers like ngspice for an RC deck, or with ``reply`` when given."""

    def __init__(self, reply: str | None = "rc") -> None:
        self.reply = reply
        self.decks: list[str] = []

    async def run(self, netlist: str) -> str | None:
        self.decks.append(netlist)
        return _rc_wrdata(netlist) if self.reply == "rc" else self.reply


def _vbus_i2c() -> Design:
    """SDA pulled up to VBUS (5 V) on an MCU and a sensor powered from +3V3;
    SCL pulled up to +3V3 as it should be."""
    return _design(
        {"U1": ("MCU", "x:y"), "U2": ("SENSOR", "x:y")}
        | {r: ("4k7", "Device:R") for r in ("R1", "R2")},
        {
            "VBUS": ["R1.1"],
            "+3V3": ["R2.1", "U1.1(VDD)", "U2.1(VDD)"],
            "SDA": ["R1.2", "U1.2(SDA)", "U2.2(SDA)"],
            "SCL": ["R2.2", "U1.3(SCL)", "U2.3(SCL)"],
        },
    )


async def test_a_flagged_i2c_line_gets_a_simulated_rise():
    from app.features.debug import calc, spice

    design = _vbus_i2c()
    fake = FakeSpice()
    calcs = await spice.attach(calc.lookup(design), design, fake)
    sda = next(c for c in calcs if "SDA" in c.text)
    scl = next(c for c in calcs if "SCL" in c.text)
    assert sda.flagged and not scl.flagged
    assert scl.simulation is None and len(fake.decks) == 1  # only the flagged line runs

    deck = fake.decks[0]
    assert "Vpull rail 0 DC 5" in deck and "Rpull rail sda 4700" in deck
    assert "Cbus sda 0 3e-11 IC=0" in deck  # 2 IC pins x 10 pF + 10 pF trace
    assert "tran " in deck and " uic" in deck and "wrdata out.txt v(sda)" in deck

    sim = sda.simulation
    assert sim is not None and (sim.v_pull, sim.v_supply) == (5.0, 3.3)
    assert len(sim.t_ns) == len(sim.v) <= 202 and sim.v[0] == 0
    # 30→70 % of an RC is ln(7/3)·RC = 0.847 × 141 ns.
    assert sim.rise_ns == pytest.approx(119.5, abs=2)
    assert "3.6" not in sim.text and "3.3 V supply + 0.3 V" in sim.text
    # The model's text is unchanged: the simulation is for the UI only.
    assert calc.format_calculations(calcs) == calc.format_calculations(calc.lookup(design))


async def test_no_simulation_when_ngspice_fails_or_answers_garbage():
    from app.features.debug import calc, spice

    design = _vbus_i2c()
    for reply in (None, "", "No. of Data Rows : 0"):
        calcs = await spice.attach(calc.lookup(design), design, FakeSpice(reply))
        assert all(c.simulation is None for c in calcs)


async def test_no_simulation_without_a_pullup_to_size():
    from app.features.debug import calc, spice

    design = _design({"U1": ("MCU", "x:y")}, {"+3V3": ["U1.1(VDD)"], "SDA": ["U1.2(SDA)"]})
    fake = FakeSpice()
    (c,) = await spice.attach(calc.lookup(design), design, fake)
    assert c.flagged and c.simulation is None and fake.decks == []


def _script(tmp_path, body: str) -> str:
    path = tmp_path / "ngspice"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


async def test_ngspice_runner_reads_wrdata_and_falls_back_to_none(tmp_path):
    from app.features.debug.spice import Ngspice

    ok = _script(tmp_path, 'test "$1" = -b && test -f "$2" && echo " 0 0" > out.txt\n')
    assert await Ngspice(binary=ok).run("* deck\n") == " 0 0\n"
    failing = _script(tmp_path, "echo boom; exit 1\n")
    assert await Ngspice(binary=failing).run("* deck\n") is None
    assert await Ngspice(binary=str(tmp_path / "missing")).run("* deck\n") is None
    slow = _script(tmp_path, "exec sleep 5\n")
    assert await Ngspice(binary=slow, timeout_s=0.2).run("* deck\n") is None


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
    assert [f["pin"] for f in summary["wiring"]] == ["U2.9"]
    assert "1 pin wired against the datasheet" in events[1][1]["message"]

    # The model reads numbered symptoms, the measurement with its step, the
    # compact netlist, the ERC with ids and the pin table.
    assert "s1: Fails on battery" in seen[0]
    assert "m1: 0.2 ohm  (for the step: Ohms L2 to VOUT)" in seen[0]
    assert "+3V3: L1.2 U2.7(VOUT) U2.9(L2)" in seen[0]
    assert "erc-1 [multiple_net_names]" in seen[0]
    assert "U2.9 L2:" in seen[0]
    assert "## Verified findings" in seen[0]
    assert "wf1: U2.9 (L2) is an inductor connection" in seen[0]

    # The structuring call gets the diagnosis, then the same input to cite.
    assert structured[0].startswith("## Diagnosis\n1. L2 tied to VOUT.")
    assert seen[0] in structured[0]

    assert events[-1][1]["diagrams"]["h1"]["ic"] == "U2"
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


async def test_review_mode_needs_no_symptoms_and_uses_the_review_prompts():
    prepared = await service.prepare(_files(), "", [], FakeKicad(), "review")
    systems: list[str] = []
    users: list[str] = []

    async def diagnose(system: str, user: str) -> str:
        systems.append(system)
        users.append(user)
        return "No issues found."

    async def structure(system: str, user: str) -> Analysis[Claim]:
        systems.append(system)
        return Analysis[Claim](hypotheses=[], open_questions=[], glossary=[])

    events = await _collect(prepared, service.Model(diagnose=diagnose, structure=structure))
    assert events[0][1]["mode"] == "review"
    assert systems == [prompt.REVIEW_DIAGNOSE, prompt.REVIEW_STRUCTURE]
    assert "## Symptoms" not in users[0]
    assert "what the board is for\n(none given)" in users[0]
    assert events[-1][0] == "result" and events[-1][1]["hypotheses"] == []


# --- route ------------------------------------------------------------------------------------


@pytest.fixture
def fake_backends(monkeypatch):
    monkeypatch.setattr(router, "KicadCli", FakeKicad)
    monkeypatch.setattr(router, "Ngspice", FakeSpice)

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


# --- reference designs ------------------------------------------------------------------------


def test_resistor_values_parse_in_kicad_styles():
    from app.features.debug.calc import named_voltage, parse_farads, parse_ohms

    assert parse_ohms("22k1R 1%") == 22100
    assert parse_ohms("4k7") == 4700
    assert parse_ohms("470R") == 470
    assert parse_ohms("1M") == 1e6
    assert parse_ohms("DNP") is None
    assert named_voltage("+3V3") == 3.3
    assert named_voltage("/5V") == 5
    assert named_voltage("VBUS") == 5
    assert named_voltage("VCC") is None
    assert parse_farads("4u7") == pytest.approx(4.7e-6)
    assert parse_farads("100nF") == pytest.approx(1e-7)
    assert parse_farads("1uF/16V") == pytest.approx(1e-6)


def _buck(top: str, bottom: str) -> Design:
    parts = {
        "U5": Part("U5", "SY8088", "Rust_Board:SY8088", ""),
        "R2": Part("R2", top, "Device:R", ""),
        "R3": Part("R3", bottom, "Device:R", ""),
    }
    nets = {
        "+3V3": [Node("R2", "1", "")],
        "Net-(U5-FB)": [Node("R2", "2", ""), Node("R3", "1", ""), Node("U5", "5", "FB")],
        "GND": [Node("R3", "2", "")],
    }
    return Design(parts, nets)


def test_a_swapped_feedback_divider_is_flagged():
    from app.features.debug import calc, reference

    swapped = calc.lookup(_buck("22k1", "100k"))
    assert len(swapped) == 1 and swapped[0].flagged
    assert swapped[0].value == pytest.approx(0.733, abs=0.01)
    assert reference.lookup(_buck("22k1", "100k"))[0].part == "SY8088"

    right = calc.lookup(_buck("100k", "22k1"))
    assert not right[0].flagged
    assert right[0].value == pytest.approx(3.31, abs=0.01)


def test_a_reference_cite_must_name_a_known_id():
    from app.features.debug.evidence import check_claim

    ctx = EvidenceContext(
        design=_buck("100k", "22k1"),
        erc={},
        pin_data=frozenset(),
        symptom_ids=frozenset(),
        measurement_ids=frozenset(),
        reference_ids=frozenset({"rd1", "cf1"}),
    )
    good = Claim(
        text="The divider sets 3.31 V.",
        evidence="reference",
        cites=[Cite(kind="reference", pin=None, net=None, id="cf1")],
    )
    bad = good.model_copy(update={"cites": [Cite(kind="reference", pin=None, net=None, id="rd9")]})
    assert not check_claim(good, ctx).downgraded
    assert check_claim(bad, ctx).downgraded


def _design(parts: dict[str, tuple[str, str]], nets: dict[str, list[str]]) -> Design:
    """parts: ref -> (value, lib). nets: name -> ["REF.PIN(FUNC)", ...]."""

    def node(token: str) -> Node:
        ref_pin, _, func = token.partition("(")
        ref, _, pin = ref_pin.partition(".")
        return Node(ref, pin, func.rstrip(")"))

    return Design(
        {r: Part(r, v, lib, "") for r, (v, lib) in parts.items()},
        {name: [node(t) for t in tokens] for name, tokens in nets.items()},
    )


def test_led_current_and_a_missing_resistor():
    from app.features.debug import calc

    led = _design(
        {"D1": ("LED", "Device:LED"), "R1": ("1k", "Device:R")},
        {"+3V3": ["R1.1"], "Net-(D1-A)": ["R1.2", "D1.2(A)"], "GND": ["D1.1(K)"]},
    )
    (c,) = calc.lookup(led)
    assert c.kind == "led_current" and not c.flagged
    assert c.value == pytest.approx(1.5, abs=0.01)  # (3.3 - 1.8) / 1k

    bare = _design({"D1": ("LED", "Device:LED")}, {"+5V": ["D1.2(A)"], "GND": ["D1.1(K)"]})
    (c,) = calc.lookup(bare)
    assert c.flagged and "no resistor" in c.text


def test_i2c_pullups_are_found_or_flagged_and_qspi_clocks_ignored():
    from app.features.debug import calc

    parts = {"U1": ("MCU", "x:y"), "U2": ("SENSOR", "x:y"), "R1": ("4k7", "Device:R")}
    design = _design(
        parts,
        {
            "+3V3": ["R1.1"],
            "IO8_SCL": ["R1.2", "U1.2(GPIO8)", "U2.1(SCL)"],
            "IO10_SDA": ["U1.3(GPIO10)", "U2.2(SDA)"],
            "QSPI_SCLK": ["U1.4(QSPI_SCLK)", "U2.3(CLK)"],
        },
    )
    calcs = calc.lookup(design)
    by_net = {c.text.split(" net ")[1].split(" ")[0]: c for c in calcs}
    assert set(by_net) == {"IO8_SCL", "IO10_SDA"}
    assert by_net["IO10_SDA"].flagged and calcs[0] is by_net["IO10_SDA"]  # flagged first
    assert by_net["IO8_SCL"].value == 4700


def test_rc_delay_and_divider():
    from app.features.debug import calc

    design = _design(
        {
            "U1": ("ESP32-C3-MINI-1", "x:y"),
            "R1": ("10k", "Device:R"),
            "C1": ("1u", "Device:C"),
            "R2": ("100k", "Device:R"),
            "R3": ("100k", "Device:R"),
        },
        {
            "+3V3": ["R1.1"],
            "EN": ["R1.2", "C1.1", "U1.8(EN)"],
            "GND": ["C1.2", "R3.2"],
            "VBAT": ["R2.1"],
            "BAT_SENSE": ["R2.2", "R3.1", "U1.5(GPIO2)"],
        },
    )
    kinds = {c.kind: c for c in calc.lookup(design)}
    assert kinds["rc_delay"].value == pytest.approx(10.0)  # ms
    assert kinds["divider"].unit == "×" and kinds["divider"].value == pytest.approx(0.5)


def test_an_i2c_pullup_above_the_ic_supply_is_flagged():
    from app.features.debug import calc

    design = _design(
        {"U1": ("MCU", "x:y"), "R1": ("10k", "Device:R")},
        {"VBUS": ["R1.1"], "+3V3": ["U1.1(VDD)"], "SDA": ["R1.2", "U1.2(SDA)"]},
    )
    (c,) = calc.lookup(design)
    assert c.flagged and "5 V" in c.text and "+3V3" in c.text


def test_an_i2c_pullup_is_compared_with_the_io_supply_not_the_core():
    from app.features.debug import calc

    design = _design(
        {"U1": ("RP2040", "x:y"), "R1": ("4k7", "Device:R")},
        {
            "+3V3": ["U1.1(IOVDD)", "R1.1"],
            "+1V1": ["U1.23(DVDD)", "U1.45(VREG_VOUT)"],
            "SDA": ["U1.6(GPIO4)", "R1.2"],
        },
    )
    (c,) = [c for c in calc.lookup(design) if c.kind == "i2c_pullup"]
    assert not c.flagged


def _usb_c(cc1: list[str], cc2: list[str], resistors: dict[str, str]) -> Design:
    """A USB-C receptacle J1 whose CC1/CC2 nets hold the given extra nodes."""
    parts = {"J1": ("USB_C_Receptacle_USB2.0", "Connector:USB_C_Receptacle_USB2.0")}
    parts |= {ref: (value, "Device:R") for ref, value in resistors.items()}
    nets = {"Net-(J1-CC1)": ["J1.A5(CC1)", *cc1], "GND": [f"{r}.2" for r in resistors]}
    if cc2 is cc1:
        nets["Net-(J1-CC1)"].append("J1.B5(CC2)")
    else:
        nets["Net-(J1-CC2)"] = ["J1.B5(CC2)", *cc2]
    return _design(parts, nets)


def test_usb_c_cc_pulldowns_are_checked():
    from app.features.debug import calc

    (good,) = calc.lookup(_usb_c(["R6.1"], ["R7.1"], {"R6": "5.1k", "R7": "5k1"}))
    assert good.kind == "usb_cc" and not good.flagged and "own 5.1 kΩ" in good.text

    shared = ["R6.1"]
    (c,) = calc.lookup(_usb_c(shared, shared, {"R6": "5.1k"}))
    assert c.flagged and "tied together" in c.text and "R6.1" in c.pins

    (c,) = calc.lookup(_usb_c(["R6.1"], [], {"R6": "5.1k"}))
    assert c.flagged and c.value is None and "CC2 (J1.B5) has no resistor" in c.text

    (c,) = calc.lookup(_usb_c(["R6.1"], ["R7.1"], {"R6": "5.1k", "R7": "56k"}))
    assert c.flagged and c.value == 56000 and "Rp" in c.text


def test_usb_c_cc_left_to_a_pd_controller_or_a_source_is_not_flagged():
    from app.features.debug import calc

    pd = _usb_c(["U1.1(CC1)"], ["U1.2(CC2)"], {})
    pd.parts["U1"] = Part("U1", "CH224K", "x:y", "")
    assert calc.lookup(pd) == []

    source = _design(
        {
            "J1": ("USB_C_Receptacle", "Connector:USB_C_Receptacle"),
            "R1": ("56k", "Device:R"),
            "R2": ("56k", "Device:R"),
        },
        {
            "VBUS": ["R1.1", "R2.1"],
            "CC1": ["J1.A5(CC1)", "R1.2"],
            "CC2": ["J1.B5(CC2)", "R2.2"],
        },
    )
    (c,) = calc.lookup(source)
    assert not c.flagged and "source port" in c.text


def test_a_supply_rail_with_no_capacitor_is_flagged():
    from app.features.debug import calc

    parts = {"U1": ("CH340C", "x:y"), "U2": ("AMS1117-3.3", "x:y"), "C1": ("100n", "Device:C")}
    bare = _design(
        parts,
        {"+5V": ["U1.16(VCC)", "U2.3(VIN)"], "+3V3": ["U2.2(VOUT)", "C1.1"], "GND": ["C1.2"]},
    )
    flagged = {c.ic: c for c in calc.lookup(bare) if c.kind == "decoupling"}
    assert set(flagged) == {"U1", "U2"} and all(c.flagged for c in flagged.values())
    assert "+5V" in flagged["U1"].text and "no capacitor" in flagged["U1"].text

    decoupled = _design(
        parts,
        {"+5V": ["U1.16(VCC)", "U2.3(VIN)", "C1.1"], "+3V3": ["U2.2(VOUT)"], "GND": ["C1.2"]},
    )
    assert not [c for c in calc.lookup(decoupled) if c.kind == "decoupling"]


def test_fewer_capacitors_than_power_pins_is_a_hint_not_a_flag():
    from app.features.debug import calc

    design = _design(
        {"U1": ("RP2040", "x:y"), "C1": ("100n", "Device:C")},
        {"+3V3": ["U1.1(IOVDD)", "U1.10(IOVDD)", "U1.48(USB_VDD)", "C1.1"], "GND": ["C1.2"]},
    )
    (c,) = calc.lookup(design)
    assert c.kind == "decoupling" and not c.flagged and "3 power pins" in c.text


def _ams1117(vin_net: str, value: str = "AMS1117-3.3") -> Design:
    return _design(
        {"U1": (value, "Regulator_Linear:AMS1117-3.3"), "C1": ("10u", "Device:C")},
        {
            vin_net: ["U1.3(VI)", "C1.1"],
            "+3V3": ["U1.2(VO)", "C1.2"],  # C1 isn't to ground: keeps decoupling out of it
            "GND": ["U1.1(GND)"],
        },
    )


def test_ams1117_dropout_is_calculated_and_flagged_below_the_typical_dropout():
    from app.features.debug import calc, reference

    (ok,) = [c for c in calc.lookup(_ams1117("+5V")) if c.kind == "dropout"]
    assert not ok.flagged and ok.value == pytest.approx(1.7)

    (low,) = [c for c in calc.lookup(_ams1117("+3V7")) if c.kind == "dropout"]
    assert low.flagged and low.value == pytest.approx(0.4) and "below the dropout" in low.text

    (cell,) = [c for c in calc.lookup(_ams1117("+BATT")) if c.kind == "dropout"]
    assert not cell.flagged and "Li-ion" in cell.text and cell.value == pytest.approx(0.9)

    # The adjustable part has no voltage in its value; the output net's name gives it.
    (adj,) = [c for c in calc.lookup(_ams1117("+5V", "AMS1117")) if c.kind == "dropout"]
    assert adj.value == pytest.approx(1.7)
    notes = reference.lookup(_ams1117("+5V"))
    assert notes and all(n.part == "AMS1117-3.3" for n in notes)
    assert any("1.1 V typical" in n.text for n in notes)


def test_ch340_notes_are_found():
    from app.features.debug import reference

    design = _design({"U1": ("CH340C", "Interface_USB:CH340C")}, {"+5V": ["U1.16(VCC)"]})
    notes = reference.lookup(design)
    assert len(notes) >= 4 and any("no series" in n.text for n in notes)
