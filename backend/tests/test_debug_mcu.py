"""A microcontroller's pins against its pin rules (``mcu.check``) and where
the rules come from (``mcu.rules``). Hand-built designs; pure functions."""

from app.features.debug import mcu
from app.features.debug.design import Design, Node, Part
from app.features.debug.pins import StrapTiming
from app.features.debug.schemas import (
    BootLevel,
    Calculation,
    PinRuleFact,
    PinRuleKind,
    PowerTree,
)

ESP = "ESP32-S3-WROOM-1"


def _part(ref: str, value: str = "", lib: str = "") -> Part:
    return Part(ref, value or ref, lib, "")


def _design(nets: dict[str, list[Node]], *extra: Part) -> Design:
    """U1, the module, plus ``extra``; every ref in ``nets`` gets a part."""
    parts = {"U1": _part("U1", ESP, "RF_Module:ESP32-S3-WROOM-1")}
    parts |= {p.ref: p for p in extra}
    for nodes in nets.values():
        for n in nodes:
            parts.setdefault(n.ref, _part(n.ref))
    return Design(parts, nets)


def _rule(
    kind: PinRuleKind, *pins: str, level: BootLevel = "either", condition: str = ""
) -> mcu.Rule:
    return mcu.Rule(
        kind=kind,
        pins=pins,
        text="The rule.",
        source="datasheet p.9",
        boot_level=level,
        condition=condition,
    )


def _check(design: Design, *rules: mcu.Rule, first_id: int = 1) -> list[Calculation]:
    return mcu.check(design, PowerTree(rails=[], parts=[]), {"U1": list(rules)}, first_id)


IO0 = Node("U1", "27", "IO0")
STRAP_HIGH = _rule("strapping", "27", level="high")


# --- strapping pins ------------------------------------------------------------------


def test_a_strapping_pin_tied_to_the_wrong_level_is_flagged():
    [found] = _check(_design({"GND": [IO0, Node("U1", "40", "GND")]}), STRAP_HIGH)
    assert found.flagged and found.kind == "mcu_pin" and found.pins == ["U1.27"]
    assert "tied low (GND)" in found.text and "must be high" in found.text
    assert 'Rule (datasheet p.9): "The rule."' in found.text


def test_a_strapping_pin_tied_to_the_right_level_is_quiet():
    assert _check(_design({"+3V3": [IO0]}), STRAP_HIGH) == []


def test_a_pull_to_the_wrong_level_is_flagged_alone_and_may_fail_with_a_load():
    alone = {"BOOT": [IO0, Node("R1", "1", "")], "GND": [Node("R1", "2", "")]}
    [found] = _check(_design(alone), STRAP_HIGH)
    assert found.flagged and "pulled low by R1" in found.text
    assert found.pins == ["U1.27", "R1.1", "R1.2"]

    loaded = {**alone, "BOOT": [*alone["BOOT"], Node("U2", "3", "OUT")]}
    [listed] = _check(_design(loaded), STRAP_HIGH)
    assert not listed.flagged and "U2 also on its net may override" in listed.text
    assert "May fail, not verified" in listed.text


def test_a_pull_up_with_a_boot_button_to_ground_is_quiet():
    nets = {
        "BOOT": [IO0, Node("R1", "1", ""), Node("SW1", "1", ""), Node("C1", "1", "")],
        "+3V3": [Node("R1", "2", "")],
        "GND": [Node("SW1", "2", ""), Node("C1", "2", "")],
    }
    assert _check(_design(nets), STRAP_HIGH) == []


def test_a_strapping_pin_with_a_load_and_no_level_may_fail():
    nets = {"IO0": [IO0, Node("D1", "2", "A")], "GND": [Node("D1", "1", "K")]}
    led = _part("D1", "red", "Device:LED")
    [found] = _check(_design(nets, led), STRAP_HIGH)
    assert not found.flagged and "no defined level at reset" in found.text


def test_pulls_both_ways_give_no_level():
    nets = {
        "IO0": [IO0, Node("R1", "1", ""), Node("R2", "1", ""), Node("U2", "1", "")],
        "+3V3": [Node("R1", "2", "")],
        "GND": [Node("R2", "2", "")],
    }
    [found] = _check(_design(nets), STRAP_HIGH)
    assert not found.flagged and "R1, R2 pull both ways" in found.text


def test_a_strapping_pin_the_boot_doesnt_depend_on_is_not_checked():
    either = _rule("strapping", "27", level="either")
    assert _check(_design({"GND": [IO0]}), either) == []


# --- reserved and input-only pins ----------------------------------------------------


def test_a_reserved_pin_wired_out_is_flagged_and_left_unconnected_is_quiet():
    io35 = Node("U1", "28", "IO35")
    wired = _design({"SDA": [io35, Node("U2", "5", "SDA")]})
    [found] = _check(wired, _rule("reserved", "28"))
    assert found.flagged and "reaches U2" in found.text
    assert _check(_design({"unconnected-(U1-IO35)": [io35]}), _rule("reserved", "28")) == []


def test_a_reserved_pin_under_a_condition_is_listed_not_flagged():
    wired = _design({"SDA": [Node("U1", "28", "IO35"), Node("U2", "5", "SDA")]})
    [found] = _check(wired, _rule("reserved", "28", condition="on modules with octal PSRAM"))
    assert not found.flagged
    assert "Not flagged: it applies on modules with octal PSRAM" in found.text


def test_an_input_only_pin_driving_an_led_is_flagged_unless_something_else_can():
    io = Node("U1", "5", "GPI34")
    nets = {
        "LED": [io, Node("R1", "1", "")],
        "LED_A": [Node("R1", "2", ""), Node("D1", "2", "A")],
        "GND": [Node("D1", "1", "K")],
    }
    led = _part("D1", "green", "Device:LED")
    [found] = _check(_design(nets, led), _rule("input_only", "5"))
    assert found.flagged and "only pin that could drive D1" in found.text

    driven = {**nets, "LED": [*nets["LED"], Node("U2", "7", "GPIO")]}
    assert _check(_design(driven, led), _rule("input_only", "5")) == []


# --- USB data lines ------------------------------------------------------------------

DM, DP = Node("U1", "13", "IO19"), Node("U1", "14", "IO20")
USB = (_rule("usb_d_minus", "13"), _rule("usb_d_plus", "14"))


def test_swapped_usb_lines_are_flagged_through_a_series_resistor_too():
    nets = {
        "USB_A": [DM, Node("J1", "A6", "D+")],
        "USB_B": [DP, Node("R5", "1", "")],
        "USB_B_C": [Node("R5", "2", ""), Node("J1", "A7", "D-")],
    }
    found = _check(_design(nets), *USB)
    assert [(c.pins[0], c.flagged) for c in found] == [("U1.13", True), ("U1.14", True)]
    assert "J1.A6 (D+)" in found[0].text and "swapped" in found[0].text
    assert found[1].pins == ["U1.14", "J1.A7"]


def test_usb_lines_wired_right_are_quiet():
    nets = {"D-": [DM, Node("J1", "A7", "DN1")], "D+": [DP, Node("J1", "A6", "DP1")]}
    assert _check(_design(nets), *USB) == []


# --- ordering and sources ------------------------------------------------------------


def test_flagged_results_come_first_and_ids_continue():
    nets = {
        "GND": [IO0],
        "SDA": [Node("U1", "28", "IO35"), Node("U2", "5", "SDA")],
    }
    found = _check(
        _design(nets), _rule("reserved", "28", condition="if octal"), STRAP_HIGH, first_id=4
    )
    assert [(c.id, c.pins[0], c.flagged) for c in found] == [
        ("cf4", "U1.27", True),
        ("cf5", "U1.28", False),
    ]


def test_rules_come_from_the_pin_table_unless_the_datasheet_gave_some():
    design = _design({"GND": [IO0]}, _part("R1", "10k", "Device:R"))
    table = mcu.rules(design)
    assert list(table) == ["U1"]
    [io0] = [r for r in table["U1"] if r.pins == ("27",)]
    assert io0.boot_level == "high" and io0.source.startswith("Espressif ESP32-S3-WROOM-1")
    [found] = mcu.check(design, PowerTree(rails=[], parts=[]), table)
    assert found.flagged and found.pins == ["U1.27"]

    read = PinRuleFact(
        kind="input_only", pins=["5"], boot_level="either", condition="", text="In.", page=12
    )
    [only] = mcu.rules(design, {"U1": [read]})["U1"]
    assert (only.kind, only.source) == ("input_only", "datasheet p.12")


# --- strapping pins: the RC against the hold time ------------------------------------

TIMING = StrapTiming(pull="high", pull_ohms=45e3, threshold=0.75, hold_s=3e-3)
STRAP_RC = mcu.Rule(
    kind="strapping",
    pins=("27",),
    text="The rule.",
    source="datasheet p.9",
    boot_level="high",
    timing=TIMING,
)
CAP = _part("C5", "100n", "Device:C")


def _boot(*extra: Node) -> dict[str, list[Node]]:
    """furby-boot-cap's GPIO0: C5 to GND and the BOOT button, plus ``extra``."""
    return {
        "BOOT": [IO0, Node("C5", "1", ""), Node("SW2", "2", ""), *extra],
        "GND": [Node("C5", "2", ""), Node("SW2", "1", "")],
    }


def test_a_cap_the_internal_pull_up_cant_charge_in_the_hold_time_is_flagged():
    [found] = _check(_design(_boot(), CAP), STRAP_RC)
    assert found.flagged and found.pins == ["U1.27", "C5.1", "C5.2"]
    assert (found.value, found.unit, found.expected) == (6.238, "ms", 3.0)
    assert "internal pull-up (45 kΩ)" in found.text and "C5 (100 nF) to GND" in found.text
    assert "τ = 4.5 ms" in found.text and "VIH (0.75 × VDD) 6.24 ms after EN" in found.text
    assert "a pull-up under 41.7 kΩ to the supply, or shrink C5 to under 48.1 nF" in found.text
    assert "May fail" not in found.text


def test_a_10k_pull_up_charges_it_in_time():
    nets = {**_boot(Node("R1", "1", "")), "+3V3": [Node("R1", "2", "")]}
    assert _check(_design(nets, CAP, _part("R1", "10k", "Device:R")), STRAP_RC) == []


def test_a_weak_external_pull_up_is_listed_not_flagged():
    nets = {**_boot(Node("R1", "1", "")), "+3V3": [Node("R1", "2", "")]}
    [found] = _check(_design(nets, CAP, _part("R1", "100k", "Device:R")), STRAP_RC)
    assert not found.flagged and found.pins[-2:] == ["R1.1", "R1.2"]
    assert "and R1 (100 kΩ)" in found.text
    assert "stronger pull-ups (21.6 kΩ in all, with the internal one)" in found.text
    assert "May fail, not verified: R1 runs from the rail before EN rises" in found.text


def test_no_cap_or_no_numbers_give_no_rc_calculation():
    no_cap = {"BOOT": [IO0, Node("SW2", "2", "")], "GND": [Node("SW2", "1", "")]}
    assert _check(_design(no_cap), STRAP_RC) == []
    assert _check(_design(_boot(), CAP), STRAP_HIGH) == []


def test_a_pull_down_strapping_pin_with_a_cap_to_the_supply_is_flagged():
    io45 = Node("U1", "26", "IO45")
    rule = mcu.Rule(
        kind="strapping",
        pins=("26",),
        text="The rule.",
        source="datasheet p.9",
        boot_level="low",
        timing=StrapTiming(pull="low", pull_ohms=45e3, threshold=0.25, hold_s=3e-3),
    )
    nets = {"VSPI": [io45, Node("C5", "1", "")], "+3V3": [Node("C5", "2", "")]}
    [found] = _check(_design(nets, CAP), rule)
    assert found.flagged and found.value == 6.238
    assert "internal pull-down" in found.text and "to the supply holds it high" in found.text
    assert "VIL (0.25 × VDD)" in found.text and "a pull-down under 41.7 kΩ to GND" in found.text


def test_the_pin_tables_timing_reaches_a_datasheets_strapping_rule():
    design = _design(_boot(), CAP)
    [io0] = [r for r in mcu.rules(design)["U1"] if r.pins == ("27",)]
    assert io0.timing == TIMING

    read = PinRuleFact(
        kind="strapping", pins=["27"], boot_level="high", condition="", text="Hi.", page=14
    )
    [rule] = mcu.rules(design, {"U1": [read]})["U1"]
    assert rule.timing == TIMING and rule.source == "datasheet p.14"
    [found] = mcu.check(design, PowerTree(rails=[], parts=[]), {"U1": [rule]})
    assert found.flagged and found.value == 6.238

    other_way = read.model_copy(update={"boot_level": "low"})
    assert mcu.rules(design, {"U1": [other_way]})["U1"][0].timing is None
