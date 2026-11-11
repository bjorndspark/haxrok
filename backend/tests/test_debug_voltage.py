"""Each IC pin on a rail against its datasheet's voltage limits
(``voltage.check``). Hand-built designs and power trees; pure functions."""

from app.features.debug import voltage
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import LimitFact, LimitKind, PowerRail, PowerTree


def _design() -> Design:
    """An MCU U1 with VDD (pin 1) and VDDA (pin 9) on +3V3 and VBAT (pin 2)
    on +BATT."""
    parts = {"U1": Part("U1", "STM32F030C8T6", "MCU_ST_STM32F0:STM32F030C8Tx", "")}
    nets = {
        "+3V3": [Node("U1", "1", "VDD"), Node("U1", "9", "VDDA")],
        "+BATT": [Node("U1", "2", "VBAT")],
        "GND": [Node("U1", "8", "VSS")],
    }
    return Design(parts, nets)


def _rail(
    net: str, lo: float, hi: float, basis: str = "fixed", inferred: bool = False
) -> PowerRail:
    return PowerRail(
        net=net,
        min=lo,
        max=hi,
        basis=basis,  # type: ignore[arg-type]
        inferred=inferred,
        note="",
        fed_by=[],
        loads=["U1"],
    )


def _tree(*rails: PowerRail) -> PowerTree:
    return PowerTree(rails=list(rails), parts=[])


def _limit(
    kind: LimitKind,
    hi: float | None,
    pins: tuple[str, ...] = (),
    lo: float | None = None,
    all_pins: bool = False,
    unit: str = "V",
) -> LimitFact:
    return LimitFact(
        parameter="Supply voltage",
        kind=kind,
        pins=list(pins),
        all_pins=all_pins,
        min=lo,
        typ=None,
        max=hi,
        unit=unit,
        conditions="",
        page=44,
    )


def test_a_known_rail_over_the_absolute_maximum_is_flagged():
    tree = _tree(_rail("+3V3", 5.0, 5.0))
    [calc] = voltage.check(_design(), tree, {"U1": [_limit("absolute_maximum", 4.0, ("1", "9"))]})
    assert (calc.id, calc.kind, calc.ic, calc.flagged) == ("cf1", "pin_voltage", "U1", True)
    assert calc.pins == ["U1.1", "U1.9"]
    assert (calc.value, calc.expected, calc.unit) == (5.0, 4.0, "V")
    assert "U1.1 (VDD), U1.9 (VDDA) on +3V3" in calc.text
    assert "above U1's absolute maximum 4 V" in calc.text
    assert '"Supply voltage", datasheet p.44' in calc.text


def test_an_inferred_rail_over_the_absolute_maximum_is_listed_not_flagged():
    tree = _tree(_rail("+BATT", 3.0, 4.2, basis="charger", inferred=True))
    [calc] = voltage.check(_design(), tree, {"U1": [_limit("absolute_maximum", 4.0, ("2",))]})
    assert not calc.flagged and calc.expected is None
    assert "Not flagged: the rail's voltage is inferred" in calc.text


def test_above_the_recommended_range_only_is_listed_not_flagged():
    tree = _tree(_rail("+3V3", 3.7, 3.7))
    limits = [_limit("absolute_maximum", 4.0, ("1",)), _limit("recommended", 3.6, ("1",), lo=2.0)]
    [calc] = voltage.check(_design(), tree, {"U1": limits})
    assert not calc.flagged and calc.expected is None
    assert "above U1's recommended operating maximum 3.6 V" in calc.text
    assert "outside the recommended range only" in calc.text


def test_below_the_recommended_minimum_is_listed():
    tree = _tree(_rail("+3V3", 1.8, 1.8))
    [calc] = voltage.check(_design(), tree, {"U1": [_limit("recommended", 3.6, ("1",), lo=2.0)]})
    assert not calc.flagged and "below U1's recommended operating minimum 2 V" in calc.text


def test_a_rail_within_its_limits_is_quiet():
    tree = _tree(_rail("+3V3", 3.3, 3.3), _rail("+BATT", 3.0, 3.6, basis="user"))
    limits = [
        _limit("absolute_maximum", 4.0, all_pins=True, lo=-0.3),
        _limit("recommended", 3.3, ("1", "9"), lo=2.0),  # on the bound: within
        _limit("characteristic", 1.0, ("1",)),  # not a rating
    ]
    assert voltage.check(_design(), tree, {"U1": limits}) == []


def test_a_pins_own_limit_wins_over_the_every_pin_one():
    tree = _tree(_rail("+3V3", 4.5, 4.5), _rail("+BATT", 4.5, 4.5))
    limits = [
        _limit("absolute_maximum", 4.0, all_pins=True),
        _limit("absolute_maximum", 6.0, ("1", "9")),
    ]
    [calc] = voltage.check(_design(), tree, {"U1": limits})
    assert calc.pins == ["U1.2"] and calc.flagged


def test_the_widest_line_of_a_table_counts_and_mv_converts():
    tree = _tree(_rail("+3V3", 4.5, 4.5))
    limits = [
        _limit("absolute_maximum", 4000, ("1",), unit="mV"),
        _limit("absolute_maximum", 5000, ("1",), unit="mV"),  # a transient rating
    ]
    assert voltage.check(_design(), tree, {"U1": limits}) == []


def test_unknown_rails_relative_limits_and_ground_are_skipped():
    tree = _tree(
        PowerRail(
            net="+3V3",
            min=None,
            max=None,
            basis="unknown",
            inferred=False,
            note="",
            fed_by=[],
            loads=[],
        ),
        _rail("GND", 0.0, 0.0),
        _rail("+BATT", 4.5, 4.5),
    )
    limits = [_limit("absolute_maximum", None, all_pins=True)]  # "VDD + 0.3 V"
    assert voltage.check(_design(), tree, {"U1": limits}) == []
    limits = [_limit("absolute_maximum", -1.0, all_pins=True)]
    assert [c.pins for c in voltage.check(_design(), tree, {"U1": limits})] == [["U1.2"]]


def test_ids_follow_the_netlists_flagged_first():
    tree = _tree(_rail("+3V3", 3.7, 3.7), _rail("+BATT", 5.0, 5.0))
    limits = [_limit("recommended", 3.6, ("1", "9")), _limit("absolute_maximum", 4.0, ("2",))]
    calcs = voltage.check(_design(), tree, {"U1": limits}, first_id=3)
    assert [(c.id, c.flagged, c.pins) for c in calcs] == [
        ("cf3", True, ["U1.2"]),
        ("cf4", False, ["U1.1", "U1.9"]),
    ]
