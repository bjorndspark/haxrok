"""Each capacitor's voltage and resistor's power against its rating
(``ratings.check``). Hand-built designs and power trees; pure functions."""

from app.features.debug import ratings
from app.features.debug.design import Design, Node, Part, parse_netlist
from app.features.debug.schemas import Calculation, PowerRail, PowerTree


def _rail(net: str, lo: float, hi: float, inferred: bool = False) -> PowerRail:
    return PowerRail(
        net=net,
        min=lo,
        max=hi,
        basis="default" if inferred else "named",
        inferred=inferred,
        note="",
        fed_by=[],
        loads=[],
    )


def _tree(*rails: PowerRail) -> PowerTree:
    return PowerTree(rails=list(rails), parts=[])


def _one(ref: str, value: str, net: str, footprint: str = "", **fields: str) -> Design:
    """``ref`` from ``net`` to GND."""
    parts = {
        ref: Part(ref, value, "Device:C" if ref[0] == "C" else "Device:R", footprint, **fields)
    }
    return Design(parts, {net: [Node(ref, "1", "")], "GND": [Node(ref, "2", "")]})


VBUS = _tree(_rail("VBUS", 5.0, 5.0))


def test_a_capacitor_above_its_rating_is_flagged():
    [calc] = ratings.check(_one("C1", "4.7uF 4V", "VBUS"), VBUS, [], first_id=7)
    assert (calc.id, calc.kind, calc.ic, calc.flagged) == ("cf7", "part_rating", "C1", True)
    assert (calc.value, calc.expected, calc.pins) == (5.0, 4.0, ["C1.1", "C1.2"])
    assert "125% of its 4 V rating" in calc.text


def test_a_capacitor_within_the_derating_margin_passes():
    # 6.3 V on 5 V is 79 %: the usual choice, not listed.
    assert ratings.check(_one("C1", "10uF 6.3V", "VBUS"), VBUS, []) == []
    assert ratings.check(_one("C1", "10uF 16V", "VBUS"), VBUS, []) == []


def test_a_capacitor_past_the_margin_is_listed_not_flagged():
    tree = _tree(_rail("VBUS", 4.75, 5.5))
    [calc] = ratings.check(_one("C1", "4u7 6V3", "VBUS"), tree, [])
    assert not calc.flagged and calc.expected is None
    assert "87% of its 6.3 V rating" in calc.text and "May fail" in calc.text


def test_over_the_rating_on_an_inferred_rail_is_listed_only():
    tree = _tree(_rail("+BATT", 3.0, 4.2, inferred=True))
    [calc] = ratings.check(_one("C1", "1uF 4V", "+BATT"), tree, [])
    assert not calc.flagged and "inferred" in calc.text


def test_the_voltage_field_beats_the_value():
    design = _one("C1", "10uF", "VBUS", voltage="4V")
    [calc] = ratings.check(design, VBUS, [])
    assert calc.flagged
    assert ratings.check(_one("C1", "10uF", "VBUS", voltage="25"), VBUS, []) == []


def test_an_unrated_capacitor_is_listed_only_from_12_v():
    assert ratings.check(_one("C1", "10uF", "VBUS"), VBUS, []) == []
    tree = _tree(_rail("+12V", 12.0, 12.0))
    [calc] = ratings.check(_one("C1", "10uF", "+12V"), tree, [])
    assert not calc.flagged and "no voltage rating" in calc.text


def test_a_capacitor_on_an_unknown_net_is_ignored():
    assert ratings.check(_one("C1", "1nF 4V", "Net-(U1-FB)"), VBUS, []) == []


def test_a_resistor_over_its_package_rating_is_flagged():
    # 5² / 100 Ω = 250 mW in an 0402 (62.5 mW).
    design = _one("R1", "100R", "VBUS", "Resistor_SMD:R_0402_1005Metric")
    [calc] = ratings.check(design, VBUS, [])
    assert calc.flagged and (calc.value, calc.unit, calc.expected) == (250.0, "mW", 62.5)
    assert "the usual rating for 0402" in calc.text


def test_a_resistor_within_its_rating_passes():
    design = _one("R1", "100k", "VBUS", "Resistor_SMD:R_0402_1005Metric")
    assert ratings.check(design, VBUS, []) == []


def test_a_resistor_past_the_margin_is_listed():
    # 25 / 270 = 92.6 mW of an 0603's 100 mW.
    design = _one("R1", "270", "VBUS", "Resistor_SMD:R_0603_1608Metric")
    [calc] = ratings.check(design, VBUS, [])
    assert not calc.flagged and "May fail" in calc.text


def test_the_power_field_beats_the_package():
    design = _one("R1", "100R", "VBUS", "Resistor_SMD:R_0402_1005Metric", power="1/2W")
    assert ratings.check(design, VBUS, []) == []


def test_a_resistor_with_no_package_or_field_is_ignored():
    assert ratings.check(_one("R1", "10R", "VBUS"), VBUS, []) == []


def test_a_resistor_in_series_with_an_led_uses_its_current():
    # 50 mA through 47 Ω = 117.5 mW in an 0603 (100 mW).
    parts = {
        "R1": Part("R1", "47", "Device:R", "Resistor_SMD:R_0603_1608Metric"),
        "D1": Part("D1", "LED", "Device:LED", ""),
    }
    nets = {
        "+5V": [Node("D1", "2", "A")],
        "Net-(D1-K)": [Node("D1", "1", "K"), Node("R1", "1", "")],
        "GND": [Node("R1", "2", "")],
    }
    led = Calculation(
        id="cf1",
        kind="led_current",
        ic="D1",
        pins=["D1.1", "D1.2", "R1.1", "R1.2"],
        value=50.0,
        unit="mA",
        expected=None,
        flagged=False,
        text="",
    )
    tree = _tree(_rail("+5V", 5.0, 5.0))
    [calc] = ratings.check(Design(parts, nets), tree, [led], first_id=2)
    assert calc.id == "cf2" and calc.flagged and calc.value == 117.5


def test_flagged_come_first():
    parts = {
        "C1": Part("C1", "10uF 6.3V", "Device:C", ""),
        "C2": Part("C2", "10uF 4V", "Device:C", ""),
    }
    nets = {"VBUS": [Node("C1", "1", ""), Node("C2", "1", "")]}
    nets["GND"] = [Node("C1", "2", ""), Node("C2", "2", "")]
    tree = _tree(_rail("VBUS", 4.75, 5.5))
    calcs = ratings.check(Design(parts, nets), tree, [])
    assert [(c.ic, c.flagged) for c in calcs] == [("C2", True), ("C1", False)]


def test_the_netlist_parser_reads_rating_fields():
    text = """(export (version "E")
      (components
        (comp (ref "C1") (value "10uF") (footprint "Capacitor_SMD:C_0603_1608Metric")
          (fields (field (name "Rated Voltage") "16V") (field (name "Footprint") "x")))
        (comp (ref "R1") (value "1k") (footprint "Resistor_SMD:R_0402_1005Metric")
          (fields (field (name "Power") "0.1W"))))
      (nets))"""
    design = parse_netlist(text)
    assert design.parts["C1"].voltage == "16V" and design.parts["R1"].power == "0.1W"
    assert ratings.capacitor_volts(design, "C1") == 16.0
    assert ratings.resistor_watts(design, "R1") == (0.1, "its Power field, 0.1W")
