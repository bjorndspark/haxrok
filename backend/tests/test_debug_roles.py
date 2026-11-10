"""Pin roles and the wiring rules built on them (``roles``), through
``pins.lookup`` where the board has no pin table or datasheet."""

from app.features.debug import pins, roles
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import AppPart, PinRole


def _design(parts: dict[str, str], nets: dict[str, list[tuple[str, str, str]]]) -> Design:
    """A design from ref -> value and net -> (ref, pin, pin name) nodes."""
    return Design(
        {ref: Part(ref, value, "", "") for ref, value in parts.items()},
        {net: [Node(*node) for node in nodes] for net, nodes in nets.items()},
    )


# A buck-boost no pin table covers, as the orbit-l2-shorted eval board has
# it: L2 drawn onto its own output rail.
_BUCK_BOOST = {"U15": "TPS63802", "L3": "470n", "C1": "10u", "U3": "STM32L072"}


def _buck_boost(l2_net: str, suffix: bool = False) -> Design:
    def pin(number: str, name: str) -> tuple[str, str, str]:
        return ("U15", number, f"{name}_{number}" if suffix else name)

    nets = {
        "+BATT": [pin("1", "VIN"), pin("2", "EN"), ("C1", "1", "")],
        "GND": [pin("3", "GND"), ("C1", "2", "")],
        "/L1": [pin("4", "L1"), ("L3", "1", "")],
        "+3.3V": [pin("6", "VOUT"), pin("7", "FB"), ("U3", "1", "VDD")],
    }
    nets.setdefault(l2_net, []).extend([pin("5", "L2"), ("L3", "2", "")])
    return _design(_BUCK_BOOST, nets)


def test_pin_names_give_roles_without_kicad_10s_suffix():
    assert roles.name_role("L2_7", "7") == "switch"
    assert roles.name_role("SW") == "switch"
    assert roles.name_role("FB") == "feedback"
    assert roles.name_role("PGND") == "ground"
    assert roles.name_role("VOUT") == "supply_out"
    assert roles.name_role("VDDIO") == "supply_in"
    assert roles.name_role("GPIO4/TOUCH4") is None


def test_a_switch_pin_on_its_own_output_is_a_finding_by_its_name():
    [finding] = pins.lookup(_buck_boost("+3.3V")).findings
    assert (finding.id, finding.pin, finding.pin_name) == ("wf1", "U15.5", "L2")
    assert finding.net == "+3.3V"
    # Its own IC's pins first, then the other IC's, then the passives.
    assert finding.conflicts == ["U15.6", "U15.7", "U3.1"]
    assert finding.inductors == ["L3.2"]
    assert "U15.5 (L2) is an inductor connection, a switch node (by its pin name)" in finding.text


def test_kicad_10_pin_names_are_read_the_same():
    [finding] = pins.lookup(_buck_boost("+3.3V", suffix=True)).findings
    assert (finding.pin, finding.pin_name) == ("U15.5", "L2")


def test_the_same_buck_boost_wired_right_is_quiet():
    # L2 on its own node; FB on VOUT is the fixed-output wiring.
    assert pins.lookup(_buck_boost("/L2")).findings == []


def test_a_switch_pin_reaching_another_ic_is_a_finding():
    design = _design(
        {"U1": "AP62200", "U2": "TLV1117", "L1": "4u7", "C5": "100n", "D1": "SS34"},
        {
            "/SW": [("U1", "3", "SW"), ("L1", "1", ""), ("C5", "2", ""), ("U2", "3", "IN")],
            "/BST": [("U1", "6", "BST"), ("C5", "1", "")],
            "/FB": [("U1", "4", "FB")],
            "+5V": [("L1", "2", "")],
        },
    )
    [finding] = pins.lookup(design).findings
    assert finding.pin == "U1.3"
    assert finding.conflicts == ["U2.3", "C5.2"]


def test_a_buck_with_its_bootstrap_cap_and_catch_diode_is_quiet():
    design = _design(
        {"U1": "AP62200", "L1": "4u7", "C5": "100n", "D1": "SS34", "R1": "100k", "R2": "22k"},
        {
            "/SW": [("U1", "3", "SW"), ("L1", "1", ""), ("C5", "2", ""), ("D1", "2", "")],
            "/BST": [("U1", "6", "BST"), ("C5", "1", "")],
            "/FB": [("U1", "4", "FB"), ("R1", "2", ""), ("R2", "1", "")],
            "+5V": [("L1", "2", ""), ("R1", "1", "")],
            "GND": [("U1", "2", "GND"), ("D1", "1", ""), ("R2", "2", "")],
        },
    )
    assert pins.lookup(design).findings == []


def test_a_switch_name_on_an_ic_that_isnt_a_regulator_says_nothing():
    # "SW" on a microcontroller is a button input, not a switch node.
    design = _design(
        {"U3": "STM32L072", "U4": "TS5A3159"},
        {"/BTN": [("U3", "12", "SW"), ("U4", "1", "NO")], "/L1": [("U4", "2", "L1")]},
    )
    assert pins.lookup(design).findings == []


def _two_rails(fb_net: str) -> Design:
    """A buck making +5V, and an LDO making +3V3 from it."""
    nets: dict[str, list[tuple[str, str, str]]] = {
        "/SW": [("U1", "3", "SW"), ("L1", "1", "")],
        "+5V": [("L1", "2", ""), ("U2", "1", "VIN")],
        "+3V3": [("U2", "5", "VOUT")],
        "GND": [("U1", "2", "GND"), ("U2", "2", "GND")],
    }
    nets.setdefault(fb_net, []).append(("U1", "4", "FB"))
    return _design({"U1": "AP62200", "U2": "TLV75533", "L1": "4u7"}, nets)


def test_a_feedback_pin_on_another_rail_is_a_finding():
    [finding] = pins.lookup(_two_rails("+3V3")).findings
    assert (finding.pin, finding.net) == ("U1.4", "+3V3")
    assert "is a supply rail, not U1's output (+5V)" in finding.text
    assert finding.conflicts == ["U2.5"]


def test_a_feedback_pin_on_its_own_output_is_quiet():
    assert pins.lookup(_two_rails("+5V")).findings == []


def test_a_feedback_pin_on_ground_counts_only_by_datasheet():
    design = _two_rails("GND")
    assert pins.lookup(design).findings == []  # a fixed version may ground FB

    def read(function: str) -> list[str]:
        lookup = pins.lookup(
            design,
            datasheet_pins={"U1": {"4": ("FB", function)}},
            datasheet_roles={"U1": {"4": "feedback"}},
        )
        return [f.pin for f in lookup.findings]

    assert read("Feedback input to the error amplifier. (p.3)") == ["U1.4"]
    assert read("Feedback. Connect to GND for the fixed 3.3 V version. (p.3)") == []


def _ldo(vin_net: str, en_net: str) -> Design:
    nets: dict[str, list[tuple[str, str, str]]] = {
        "+3V3": [("U2", "5", "VOUT"), ("U2", "4", "FB")],
        "GND": [("U2", "2", "GND")],
    }
    nets.setdefault(vin_net, []).append(("U2", "1", "VIN"))
    nets.setdefault(en_net, []).append(("U2", "3", "EN"))
    return _design({"U2": "TLV75533"}, nets)


_LDO_ROLES: dict[str, dict[str, PinRole]] = {
    "U2": {"1": "supply_in", "2": "ground", "3": "enable", "4": "feedback", "5": "supply_out"}
}


def _app(role: str, kind: str, pin_numbers: list[str]) -> AppPart:
    return AppPart(
        role=role,
        kind=kind,
        value="1 µF",
        pins=pin_numbers,
        page=12,
        min=None,
        max=None,
        unit="",
        range_page=None,
        required=True,
    )


def test_a_typical_application_part_shorted_on_the_board_is_a_finding():
    design = _ldo("GND", "/EN")
    application = {"U2": [_app("input capacitor", "capacitor", ["1", "2"])]}
    [finding] = pins.lookup(design, datasheet_roles=_LDO_ROLES, application=application).findings
    assert (finding.pin, finding.conflicts, finding.net) == ("U2.1", ["U2.2"], "GND")
    assert "puts its input capacitor (capacitor 1 µF, p.12) between U2.1 (VIN)" in finding.text
    assert "so the capacitor is shorted out" in finding.text


def test_pins_a_datasheet_lets_a_board_join_are_not_a_short():
    # EN tied to VIN, and a feed-forward capacitor from FB to the output it
    # senses: both are how the datasheet draws it.
    design = _ldo("+5V", "+5V")
    application = {
        "U2": [
            _app("input capacitor", "capacitor", ["1", "3"]),
            _app("feed-forward capacitor", "capacitor", ["4", "5"]),
        ]
    }
    lookup = pins.lookup(design, datasheet_roles=_LDO_ROLES, application=application)
    assert lookup.findings == []
