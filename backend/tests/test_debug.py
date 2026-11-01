"""Debug feature tests: the KiCad parsers and the evidence checker.

The checker runs against the real netlist of the ``demo`` eval case, so its rules are tested on the bug the demo is
built around. The case stores the compacted netlist; ``_load_case_design``
reads that form back into a ``Design``.
"""

import json
from pathlib import Path

import pytest

from app.features.debug.design import (
    Design,
    Node,
    Part,
    compact,
    format_erc,
    normalize_pin,
    parse_erc,
    parse_netlist,
)
from app.features.debug.evidence import EvidenceContext, check, check_claim
from app.features.debug.schemas import (
    Analysis,
    Cite,
    Claim,
    ConfirmStep,
    ErcEntry,
    Fix,
    Hypothesis,
    Reading,
    SetupChange,
)

# tests/../../evals: the repo's evals/ in a checkout, /evals in the container.
CASE = Path(__file__).resolve().parents[2] / "evals" / "cases" / "rust-board-fb-swapped"


def _load_case_design() -> Design:
    """Rebuild a Design from the case's compact netlist ('NET: REF.PIN(FUNC) ...')."""
    parts: dict[str, Part] = {}
    nets: dict[str, list[Node]] = {}
    section = ""
    for line in (CASE / "netlist.txt").read_text().splitlines():
        if line.startswith("# "):
            section = line
        elif line and section == "# Components":
            ref, value, *_ = line.split()
            parts[ref] = Part(ref, value, "", "")
        elif line and section == "# Nets":
            name, _, pins = line.partition(": ")
            nodes = []
            for tok in pins.split():
                pin_ref, _, func = tok.partition("(")
                ref, _, pin = pin_ref.partition(".")
                nodes.append(Node(ref, pin, func.rstrip(")")))
            nets[name] = nodes
    return Design(parts, nets)


@pytest.fixture(scope="module")
def ctx() -> EvidenceContext:
    # Hand-copied from the case's erc.txt (text report); the backend itself
    # reads kicad-cli's JSON form, covered by test_parse_erc_*.
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
        design=_load_case_design(),
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
    (net (code "2") (name "/Power/SW1")
      (node (ref "L1") (pin "1") (pintype "passive"))
      (node (ref "U2") (pin "11") (pinfunction "L1") (pintype "passive")))))
"""


def test_parse_netlist_reads_parts_and_nets():
    design = parse_netlist(NETLIST)
    assert design.parts["U2"].footprint == "Package_DFN_QFN:Texas_VQFN-15"
    assert design.net_of("U2.9") == "+3V3"
    assert design.resolve_net("SW1") == "/Power/SW1"
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
                "path": "/Power/",
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


def test_format_erc_says_when_clean():
    assert format_erc([]) == "(no ERC violations)\n"
