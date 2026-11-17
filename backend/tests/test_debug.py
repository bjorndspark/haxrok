"""Debug feature tests: the KiCad parsers and the evidence checker.

The checker runs against a small synthetic buck-boost design with its L2
switch node shorted onto the +3V3 output rail, the classic wiring fault the
checker's rules are written for.
"""

import dataclasses
import json
import pathlib

import pytest

from app.features.debug.design import (
    Design,
    Node,
    Part,
    compact,
    format_erc,
    normalize_pin,
    parse_drc,
    parse_erc,
    parse_netlist,
)
from app.features.debug.evidence import EvidenceContext, check, check_claim
from app.features.debug.kicad import skip_reason
from app.features.debug.schemas import (
    Analysis,
    Cite,
    Claim,
    ConfirmStep,
    DrcEntry,
    ErcEntry,
    Fix,
    Hypothesis,
    LayoutFact,
    Reading,
    SetupChange,
)


def _buck_boost_design() -> Design:
    """A buck-boost (U2) whose L2 pin sits on +3V3 next to VOUT: the fault."""
    parts = {
        "U2": Part("U2", "TPS63070", "", ""),
        "L1": Part("L1", "1u5", "", ""),
        "C1": Part("C1", "10u", "", ""),
    }
    nets = {
        "+3V3": [
            Node("U2", "7", "VOUT"),
            Node("U2", "8", "VOUT"),
            Node("U2", "9", "L2"),
            Node("L1", "2", ""),
        ],
        "/Power/SW1": [Node("U2", "11", "L1"), Node("L1", "1", "")],
        "VDD": [Node("U2", "12", "VIN"), Node("U2", "14", "EN"), Node("C1", "1", "")],
    }
    return Design(parts, nets)


@pytest.fixture(scope="module")
def ctx() -> EvidenceContext:
    # The text-report form of the entry; the backend itself reads kicad-cli's
    # JSON form, covered by test_parse_erc_*.
    merged = ErcEntry(
        id="erc-1",
        sheet="/Power/",
        rule="multiple_net_names",
        severity="warning",
        message="Both +3V3 and SW2 are attached to the same items; "
        "+3V3 will be used in the netlist",
        items=["Symbol #PWR35 Pin 1 [Power input, Line]", "Label 'SW2'"],
    )
    return EvidenceContext(
        design=_buck_boost_design(),
        erc={"erc-1": merged},
        pin_data=frozenset({"U2.9", "U2.7", "U2.11"}),
        symptom_ids=frozenset({"s1", "s2", "s3"}),
        measurement_ids=frozenset({"m1"}),
    )


def pin(p: str, net: str) -> Cite:
    return Cite(kind="pin_on_net", pin=p, net=net, id=None)


def ref(kind: str, id_: str | None = None, p: str | None = None) -> Cite:
    return Cite(kind=kind, pin=p, net=None, id=id_)


def claim(text: str, evidence: str, *cites: Cite) -> Claim:
    return Claim(text=text, evidence=evidence, cites=list(cites))


# --- claims ------------------------------------------------------------------------


def test_schematic_claim_with_resolving_cite_keeps_label(ctx):
    c = check_claim(claim("U2.9 (L2) is on the +3V3 net.", "schematic", pin("U2.9", "+3V3")), ctx)
    assert c.evidence == "schematic"
    assert not c.downgraded


def test_pin_on_wrong_net_is_downgraded(ctx):
    c = check_claim(claim("U2.9 is on VDD.", "schematic", pin("U2.9", "VDD")), ctx)
    assert c.downgraded
    assert c.evidence == "none"
    assert c.claimed_evidence == "schematic"
    assert "+3V3" in (c.reason or "")


def test_pin_that_does_not_exist_is_downgraded(ctx):
    c = check_claim(claim("U2.99 is on +3V3.", "schematic", pin("U2.99", "+3V3")), ctx)
    assert c.downgraded
    assert "isn't in your schematic" in (c.reason or "")


def test_mentioned_but_uncited_pin_is_downgraded(ctx):
    # The review's case: two VOUT pins stated, only one cited.
    c = check_claim(
        claim("U2.7 and U2.8 (VOUT) are on +3V3.", "schematic", pin("U2.7", "+3V3")), ctx
    )
    assert c.downgraded
    assert "U2.8" in (c.reason or "")


def test_prose_pin_form_is_recognised(ctx):
    c = check_claim(claim("U2 pin 8 is on +3V3.", "schematic", pin("U2.7", "+3V3")), ctx)
    assert c.downgraded
    assert "U2.8" in (c.reason or "")


def test_names_glued_to_japanese_prose_still_count(ctx):
    # Japanese has no spaces: "+3V3に" must name +3V3, and "U2.8が" U2.8.
    ok = check_claim(claim("U2.9は+3V3に接続。", "schematic", pin("U2.9", "+3V3")), ctx)
    assert not ok.downgraded
    c = check_claim(claim("U2.7とU2.8が+3V3に接続。", "schematic", pin("U2.7", "+3V3")), ctx)
    assert c.downgraded
    assert "U2.8" in (c.reason or "")


def test_mentioned_but_uncited_net_is_downgraded(ctx):
    c = check_claim(claim("U2.9 is on +3V3, next to VDD.", "schematic", pin("U2.9", "+3V3")), ctx)
    assert c.downgraded
    assert "VDD" in (c.reason or "")


def test_hierarchical_net_resolves_by_short_name(ctx):
    c = check_claim(claim("U2.11 is on SW1.", "schematic", pin("U2.11(L1)", "SW1")), ctx)
    assert not c.downgraded


def test_pin_name_from_netlist_backs_a_schematic_claim(ctx):
    c = check_claim(
        claim("U2 pin 9 is the L2 switching node.", "schematic", ref("pin_name", p="U2.9")), ctx
    )
    assert not c.downgraded


def test_pin_name_must_match_the_netlist(ctx):
    c = check_claim(claim("U2.9 is the VIN pin.", "schematic", ref("pin_name", p="U2.9")), ctx)
    assert c.downgraded
    assert "L2" in (c.reason or "")


def test_overbar_markup_is_ignored_in_pin_names():
    design = Design({}, {"/RST": [Node("U4", "2", "~{RESET}")]})
    ctx = EvidenceContext(design, {}, frozenset(), frozenset(), frozenset())
    c = check_claim(claim("U4.2 is RESET.", "schematic", ref("pin_name", p="U4.2")), ctx)
    assert not c.downgraded


@pytest.mark.parametrize(
    ("text", "part", "num"),
    [
        ("R3.2 is on net XOUT.", "R3", "2"),
        ("J1.A1 is on net XOUT.", "J1", "A1"),
        ("R3 pin 2 is on net XOUT.", "R3", "2"),
    ],
)
def test_net_named_like_a_pin_number_is_not_found_inside_a_pin_ref(text, part, num):
    # rp-micro has GPIO header nets literally named "2" and "A1".
    design = Design(
        {},
        {
            "XOUT": [Node(part, num, "")],
            "2": [Node("J2", "5", "")],
            "A1": [Node("J4", "7", "")],
        },
    )
    ctx = EvidenceContext(design, {}, frozenset(), frozenset(), frozenset())
    c = check_claim(claim(text, "schematic", pin(f"{part}.{num}", "XOUT")), ctx)
    assert not c.downgraded, c.reason


def test_net_named_like_a_number_still_counts_on_its_own():
    design = Design({}, {"XOUT": [Node("R3", "2", "")], "2": [Node("J2", "5", "")]})
    ctx = EvidenceContext(design, {}, frozenset(), frozenset(), frozenset())
    c = check_claim(claim("R3.2 is on XOUT, next to net 2.", "schematic", pin("R3.2", "XOUT")), ctx)
    assert c.downgraded
    assert "net 2" in (c.reason or "")


def test_label_needs_a_cite_of_its_kind(ctx):
    c = check_claim(claim("Two net names were merged.", "schematic", ref("erc", "erc-1")), ctx)
    assert c.downgraded
    assert "cites no pin_name or pin_on_net" in (c.reason or "")


def test_erc_claim_may_name_nets_from_its_entry(ctx):
    c = check_claim(
        claim("KiCad warned that +3V3 and SW2 were merged.", "erc", ref("erc", "erc-1")), ctx
    )
    assert not c.downgraded


def test_unknown_erc_entry_is_downgraded(ctx):
    c = check_claim(claim("KiCad flagged a merge.", "erc", ref("erc", "erc-9")), ctx)
    assert c.downgraded


@pytest.fixture(scope="module")
def board_ctx(ctx) -> EvidenceContext:
    """The ``ctx`` design with a board: one DRC entry and one layout fact
    about U2.12 (VIN) and C1."""
    return dataclasses.replace(
        ctx,
        drc={
            "drc-1": DrcEntry(
                id="drc-1",
                rule="unconnected_items",
                severity="error",
                message="Missing connection between items",
                items=["Pad 12 [VDD] of U2 on F.Cu", "Pad 1 [VDD] of C1 on F.Cu"],
            )
        },
        layout={
            "pl1": LayoutFact(
                id="pl1",
                kind="decoupling",
                refs=["U2", "C1"],
                pins=["U2.12", "C1.1"],
                net="VDD",
                value=12.0,
                limit=5.0,
                unit="mm",
                flagged=True,
                text="U2.12 (VIN, VDD): nearest decoupling capacitor C1, 12.0 mm.",
            )
        },
    )


def test_board_claim_citing_a_layout_fact_may_name_its_pins_and_net(board_ctx):
    c = check_claim(
        claim("C1.1 is 12 mm of copper from U2.12 on VDD.", "board", ref("layout", "pl1")),
        board_ctx,
    )
    assert c.evidence == "board"
    assert not c.downgraded


def test_board_claim_citing_a_drc_entry_keeps_its_label(board_ctx):
    c = check_claim(
        claim("KiCad's DRC finds VDD unrouted.", "board", ref("drc", "drc-1")), board_ctx
    )
    assert not c.downgraded


@pytest.mark.parametrize("cite", [ref("layout", "pl9"), ref("drc", "drc-9")])
def test_unknown_board_cite_is_downgraded(board_ctx, cite):
    c = check_claim(claim("The board has a problem.", "board", cite), board_ctx)
    assert c.downgraded


def test_board_claim_needs_a_board_cite(board_ctx):
    c = check_claim(claim("The layout is poor.", "board", ref("erc", "erc-1")), board_ctx)
    assert c.downgraded
    assert "cites no drc or layout" in (c.reason or "")


def test_datasheet_claim_needs_pin_table_entry(ctx):
    kept = check_claim(
        claim("U2.9 is the boost-side switch node.", "datasheet", ref("pin_function", p="U2.9")),
        ctx,
    )
    dropped = check_claim(
        claim("U2.14 is the enable pin.", "datasheet", ref("pin_function", p="U2.14")), ctx
    )
    assert not kept.downgraded
    assert dropped.downgraded
    assert "pin table" in (dropped.reason or "")


def test_measurement_claim_needs_entered_id(ctx):
    kept = check_claim(
        claim("It boots after the fix.", "measurement", ref("measurement", "m1")), ctx
    )
    dropped = check_claim(claim("It reads 0 Ω.", "measurement", ref("measurement", "m7")), ctx)
    assert not kept.downgraded
    assert dropped.downgraded


def test_claim_without_evidence_is_left_alone(ctx):
    c = check_claim(claim("In boost mode the chip pulls L2 to ground.", "none"), ctx)
    assert c.evidence == "none"
    assert not c.downgraded


@pytest.mark.parametrize("raw", ["U2.9", "U2.9(L2)", " U2 pin 9 ", "U2 pin 9 (L2)"])
def test_normalize_pin(raw):
    assert normalize_pin(raw) == "U2.9"


# --- whole analysis ------------------------------------------------------------------


def _step(step_id: str, setup: list[SetupChange], supports: list[str]) -> ConfirmStep:
    return ConfirmStep(
        id=step_id,
        purpose="test_cause",
        instruction="Rewire, then power from battery.",
        setup=setup,
        tool="multimeter",
        power="on",
        practical=False,
        practical_note="U2 is a QFN.",
        readings=[
            Reading(
                reading="Boots",
                establishes="The miswiring caused it.",
                supports=supports,
                rules_out=[],
            )
        ],
    )


def _hypothesis(
    hid: str, rank: int, explains: list[str], confirm: list[ConfirmStep]
) -> Hypothesis[Claim]:
    return Hypothesis[Claim](
        id=hid,
        rank=rank,
        severity=None,
        title="t",
        summary="s",
        claims=[claim("U2.9 is on +3V3.", "schematic", pin("U2.9", "+3V3"))],
        explains=explains,
        does_not_explain=[],
        confirm=confirm,
        fix=Fix(schematic=[], rework=[]),
    )


def _analysis(*hs: Hypothesis[Claim]) -> Analysis[Claim]:
    return Analysis[Claim](hypotheses=list(hs), open_questions=[], glossary=[])


def test_correct_rewire_setup_passes(ctx):
    # The full correction: L2 off the rail, inductor moved between L1 and L2.
    setup = [
        SetupChange(action="disconnect", pin="U2.9", net="+3V3", to_pin=None),
        SetupChange(action="disconnect", pin="L1.2", net="+3V3", to_pin=None),
        SetupChange(action="connect", pin="L1.2", net=None, to_pin="U2.9"),
    ]
    result = check(_analysis(_hypothesis("h1", 1, ["s1"], [_step("h1-c1", setup, ["h1"])])), ctx)
    assert result.problems == []


def test_setup_with_wrong_net_is_reported(ctx):
    setup = [SetupChange(action="disconnect", pin="U2.9", net="VDD", to_pin=None)]
    result = check(_analysis(_hypothesis("h1", 1, [], [_step("h1-c1", setup, [])])), ctx)
    assert [p.where for p in result.problems] == ["h1-c1"]


def test_connect_needs_exactly_one_target(ctx):
    setup = [SetupChange(action="connect", pin="L1.2", net="+3V3", to_pin="U2.9")]
    result = check(_analysis(_hypothesis("h1", 1, [], [_step("h1-c1", setup, [])])), ctx)
    assert "neither or both" in result.problems[0].message


def test_unknown_ids_are_removed_and_reported(ctx):
    h = _hypothesis("h1", 1, ["s1", "s9"], [_step("h1-c1", [], ["h1", "h7"])])
    result = check(_analysis(h), ctx)
    checked = result.hypotheses[0]
    assert checked.explains == ["s1"]
    assert checked.confirm[0].readings[0].supports == ["h1"]
    assert {p.message for p in result.problems} == {
        "Removed unknown symptom s9.",
        "Removed unknown hypothesis h7.",
    }


def test_duplicate_ids_are_reported(ctx):
    result = check(_analysis(_hypothesis("h1", 1, [], []), _hypothesis("h1", 2, [], [])), ctx)
    assert any("share the id h1" in p.message for p in result.problems)


def test_hypotheses_come_back_in_rank_order(ctx):
    result = check(_analysis(_hypothesis("h2", 2, [], []), _hypothesis("h1", 1, [], [])), ctx)
    assert [h.id for h in result.hypotheses] == ["h1", "h2"]
    assert result.hypotheses[0].claims[0].evidence == "schematic"


# --- strict output schema ---------------------------------------------------------------


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


def test_model_output_schema_is_strict_mode_compatible():
    """Strict structured output: every object lists all its properties as
    required, forbids extras, and nothing carries a default."""
    schema = Analysis[Claim].model_json_schema()
    objects = list(_objects(schema))
    assert objects
    for obj in objects:
        assert obj.get("additionalProperties") is False, obj.get("title")
        assert set(obj.get("required", [])) == set(obj["properties"]), obj.get("title")
    assert '"default"' not in json.dumps(schema)


# --- KiCad parsers ----------------------------------------------------------------------

NETLIST = """(export (version "E")
  (components
    (comp (ref "U2")
      (value "TPS63070")
      (footprint "Package_DFN_QFN:Texas_VQFN-15")
      (libsource (lib "Regulator_Switching") (part "TPS63070") (description "")))
    (comp (ref "L1")
      (value "1u5")
      (footprint "Inductor_SMD:L_1210")
      (libsource (lib "Device") (part "L_Small") (description ""))))
  (nets
    (net (code "1") (name "+3V3")
      (node (ref "L1") (pin "2") (pintype "passive"))
      (node (ref "U2") (pin "9") (pinfunction "L2") (pintype "passive")))
    (net (code "2") (name "/Power Management/SW1")
      (node (ref "L1") (pin "1") (pintype "passive"))
      (node (ref "U2") (pin "11") (pinfunction "L1") (pintype "passive")))))
"""


def test_parse_netlist_reads_parts_and_nets():
    design = parse_netlist(NETLIST)
    assert design.parts["U2"].footprint == "Package_DFN_QFN:Texas_VQFN-15"
    assert design.net_of("U2.9") == "+3V3"
    assert design.resolve_net("SW1") == "/Power Management/SW1"
    assert design.nets["+3V3"][1].function == "L2"


def test_compact_includes_footprints_and_pin_functions():
    text = compact(parse_netlist(NETLIST))
    assert "U2  TPS63070  (Regulator_Switching:TPS63070)  [Package_DFN_QFN:Texas_VQFN-15]" in text
    assert "+3V3: L1.2 U2.9(L2)" in text


@pytest.mark.parametrize("bad", ["", "(kicad_sch (version 1))", "(export (nets)"])
def test_parse_netlist_rejects_non_netlists(bad):
    with pytest.raises(ValueError):
        parse_netlist(bad)


def _violation(type_: str, desc: str) -> dict:
    return {
        "type": type_,
        "severity": "warning",
        "description": desc,
        "items": [{"description": "Label 'SW2'", "pos": {"x": 1, "y": 2}, "uuid": "u"}],
    }


def test_parse_erc_drops_library_noise_and_numbers_the_rest():
    report = {
        "sheets": [
            {"path": "/", "violations": [_violation("lib_symbol_issues", "Symbol 'GND' modified")]},
            {
                "path": "/Power Management/",
                "violations": [
                    _violation("multiple_net_names", "Both +3V3 and SW2 are attached"),
                    _violation("pin_not_driven", "Input pin not driven"),
                ],
            },
        ]
    }
    entries = parse_erc(json.dumps(report))
    assert [(e.id, e.rule) for e in entries] == [
        ("erc-1", "multiple_net_names"),
        ("erc-2", "pin_not_driven"),
    ]
    assert entries[0].items == ["Label 'SW2'"]
    assert "erc-1 [multiple_net_names]" in format_erc(entries)


def test_parse_drc_keeps_parity_unconnected_and_electrical_findings():
    # Recorded: kicad-cli 9.0.2 `pcb drc --schematic-parity --severity-all` on
    # KiCad's ecc83-pp demo board with footprint R1 deleted.
    report = (pathlib.Path(__file__).parent / "golden" / "drc_kicad9.json").read_text()
    entries = parse_drc(report)
    # Dropped: 13 lib_footprint_issues, 2 silk_edge_clearance, and 5
    # footprint_symbol_mismatch that differ only in the library nickname.
    assert [(e.id, e.rule) for e in entries] == [
        ("drc-1", "missing_footprint"),
        ("drc-2", "track_dangling"),
    ]
    assert entries[0].message == "Missing footprint R1 (1.5K)"
    assert entries[1].items == ["Track [Net-(U1A-K)] on bottom_cu, length 0.8890 mm"]


def test_parse_drc_keeps_a_footprint_mismatch_that_names_another_footprint():
    def mismatch(board: str, symbol: str) -> dict[str, object]:
        return {
            "type": "footprint_symbol_mismatch",
            "severity": "warning",
            "description": f"{board} doesn't match footprint given by symbol ({symbol}).",
            "items": [{"description": "Footprint U1"}],
        }

    report = {
        "schematic_parity": [
            mismatch("Package_TO_SOT_SMD:SOT-23", "Package_TO_SOT_SMD:SOT-23-5"),
            mismatch("Lib:SOT-23-5", "Package_TO_SOT_SMD:SOT-23-5"),
        ],
        "unconnected_items": [],
        "violations": [_violation("shorting_items", "Items shorting two nets")],
    }
    entries = parse_drc(json.dumps(report))
    assert [(e.id, e.rule) for e in entries] == [
        ("drc-1", "footprint_symbol_mismatch"),
        ("drc-2", "shorting_items"),
    ]
    assert "SOT-23 doesn't match" in entries[0].message


def test_a_board_kicad_cant_load_names_the_kicad_that_saved_it():
    board = (
        b'(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n\t(generator_version "10.0")'
    )
    error = "kicad-cli couldn't read the board. If it was saved by a newer KiCad ..."
    assert skip_reason(board, error).startswith("The board was saved by KiCad 10.0, which")
    timeout = "kicad-cli took longer than 60 s on this project."
    assert (
        skip_reason(board, timeout) == f"DRC was skipped and the run is schematic-only: {timeout}"
    )


def test_format_erc_says_when_clean():
    assert format_erc([]) == "(no ERC violations)\n"
