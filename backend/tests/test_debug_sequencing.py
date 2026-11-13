"""The order an IC's supplies come up in against its datasheet's
(``sequencing.check``). Hand-built designs and power trees; pure functions.

The board: U1, a microcontroller with VDD on pin 1 and VDDA on pin 9 that
needs VDDA up first (as the STM32F030 does); U2 makes +3V3 and U3 makes
the rail on VDDA, both from +BATT unless a test says otherwise.
"""

from app.features.debug import sequencing
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import (
    PowerPart,
    PowerPartKind,
    PowerRail,
    PowerTree,
    SequenceOrder,
    SequencingFact,
)


def _fact(order: SequenceOrder = "first", time_limit: str = "") -> SequencingFact:
    return SequencingFact(
        first=["9"],
        then=["1"],
        order=order,
        time_limit=time_limit,
        text="VDDA must be provided first",
        page=13,
    )


def _rail(net: str, source: bool = False) -> PowerRail:
    return PowerRail(
        net=net,
        source="battery" if source else None,
        min=3.3,
        max=3.3,
        basis="named",
        inferred=False,
        note="",
        fed_by=[],
        loads=[],
    )


def _part(ref: str, inputs: list[str], output: str, kind: PowerPartKind = "regulator"):
    return PowerPart(ref=ref, value=ref, kind=kind, inputs=inputs, output=output)


def _board(
    nets: dict[str, list[tuple[str, str, str]]], parts: list[PowerPart]
) -> tuple[Design, PowerTree]:
    """A design from {net: [(ref, pin, function)]} and its tree: every
    net a part makes or feeds from is a rail, +BATT the source."""
    refs = {ref for nodes in nets.values() for ref, _, _ in nodes}
    design = Design(
        {r: Part(r, r, "Device:R" if r[0] == "R" else "x:IC", "") for r in refs},
        {net: [Node(*n) for n in nodes] for net, nodes in nets.items()},
    )
    rails = dict.fromkeys(["+BATT", *(n for p in parts for n in [*p.inputs, p.output])])
    tree = PowerTree(rails=[_rail(n, source=n == "+BATT") for n in rails], parts=parts)
    return design, tree


def _check(design: Design, tree: PowerTree, fact: SequencingFact | None = None):
    return sequencing.check(design, tree, {"U1": [fact or _fact()]}, first_id=4)


REGULATORS = [_part("U2", ["+BATT"], "+3V3"), _part("U3", ["+BATT"], "VDDA")]


def test_an_analog_rail_enabled_from_the_digital_one_is_flagged():
    design, tree = _board(
        {
            "+BATT": [("U2", "1", "VIN"), ("U2", "3", "EN"), ("U3", "1", "VIN")],
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("U3", "3", "EN")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        },
        REGULATORS,
    )
    [calc] = _check(design, tree)
    assert (calc.id, calc.kind, calc.ic, calc.flagged) == ("cf4", "rail_order", "U1", True)
    assert calc.pins == ["U1.9", "U1.1"]
    assert (calc.value, calc.expected) == (None, None)
    assert "pins 9 (VDDA) up before or with pins 1 (+3V3)" in calc.text
    assert "p.13" in calc.text and "enabled from +3V3" in calc.text
    assert "brings +3V3 up first" in calc.text


def test_a_regulator_fed_from_the_rail_it_must_lead_is_flagged():
    design, tree = _board(
        {
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("U3", "1", "VIN")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        },
        [_part("U2", ["+BATT"], "+3V3"), _part("U3", ["+3V3"], "VDDA")],
    )
    [calc] = _check(design, tree)
    assert calc.flagged


def test_both_supplies_on_one_rail_pass():
    design, tree = _board(
        {"+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("U1", "9", "VDDA")]},
        [_part("U2", ["+BATT"], "+3V3")],
    )
    assert _check(design, tree) == []


def test_a_ferrite_between_the_rails_passes():
    design, tree = _board(
        {
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("FB1", "1", "")],
            "+3V3A": [("FB1", "2", ""), ("U1", "9", "VDDA")],
        },
        [_part("U2", ["+BATT"], "+3V3"), _part("FB1", ["+3V3"], "+3V3A", "pass")],
    )
    assert _check(design, tree) == []


def test_the_right_way_round_passes():
    # +3V3's regulator is enabled from VDDA: VDDA is up first.
    design, tree = _board(
        {
            "+BATT": [("U2", "1", "VIN"), ("U3", "1", "VIN")],
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA"), ("U2", "3", "EN")],
        },
        REGULATORS,
    )
    assert _check(design, tree) == []


def test_a_power_good_chain_sets_the_order():
    # U3's PG drives U2's EN: +3V3 waits for VDDA. The other way round, it's flagged.
    nets = {
        "+BATT": [("U2", "1", "VIN"), ("U3", "1", "VIN")],
        "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD")],
        "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        "/SEQ": [("U3", "4", "PG"), ("U2", "3", "EN")],
    }
    assert _check(*_board(nets, REGULATORS)) == []
    nets["/SEQ"] = [("U2", "4", "PGOOD"), ("U3", "3", "EN")]
    [calc] = _check(*_board(nets, REGULATORS))
    assert calc.flagged


def test_an_enable_pulled_up_to_the_other_rail_sets_the_order():
    design, tree = _board(
        {
            "+BATT": [("U2", "1", "VIN"), ("U3", "1", "VIN")],
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("R1", "1", "")],
            "/EN": [("R1", "2", ""), ("U3", "3", "EN")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        },
        REGULATORS,
    )
    [calc] = _check(design, tree)
    assert calc.flagged


def test_independent_regulators_may_fail():
    design, tree = _board(
        {
            "+BATT": [("U2", "1", "VIN"), ("U2", "3", "EN"), ("U3", "1", "VIN"), ("U3", "3", "EN")],
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        },
        REGULATORS,
    )
    [calc] = _check(design, tree)
    assert not calc.flagged and "May fail" in calc.text
    assert "U2 (U2) from +BATT, enabled from +BATT" in calc.text


def test_together_on_two_rails_may_fail_with_its_time_limit_quoted():
    design, tree = _board(
        {
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD"), ("U3", "3", "EN")],
            "VDDA": [("U3", "5", "VOUT"), ("U1", "9", "VDDA")],
        },
        REGULATORS,
    )
    [calc] = _check(design, tree, _fact("together", "within 10 ms"))
    assert not calc.flagged
    assert "up together, within 10 ms" in calc.text and "May fail" in calc.text


def test_a_pin_off_the_power_tree_is_skipped():
    design, tree = _board(
        {
            "+3V3": [("U2", "5", "VOUT"), ("U1", "1", "VDD")],
            "/VDDA": [("U1", "9", "VDDA")],
        },
        [_part("U2", ["+BATT"], "+3V3")],
    )
    assert _check(design, tree) == []
