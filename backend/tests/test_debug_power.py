"""The power tree built from the netlist: sources, the parts that make one
rail from another, and each rail's voltage and where it came from. Small
hand-built designs; pure functions, no model."""

import pytest

from app.features.debug import power, prompt
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import (
    Calculation,
    CheckedAnalysis,
    DesignSummary,
    LimitFact,
    PowerRail,
    PowerTree,
)


def _rail(tree: PowerTree, net: str) -> PowerRail:
    return next(r for r in tree.rails if r.net == net)


def _usb_ldo_chain() -> Design:
    """USB-C VBUS through fuse F1 to +5V, Schottky D1 to RAW, an RT9013-33
    LDO U2 to an unnamed rail, an RP2040-style MCU U1 on it."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "F1": Part("F1", "500mA", "Device:Polyfuse", ""),
        "D1": Part("D1", "B5819W", "Device:D_Schottky", ""),
        "U2": Part("U2", "RT9013-33GB", "Regulator_Linear:RT9013", ""),
        "U1": Part("U1", "RP2040", "MCU_RaspberryPi:RP2040", ""),
        "C1": Part("C1", "1uF", "Device:C", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("F1", "1", "")],
        "+5V": [Node("F1", "2", ""), Node("D1", "2", "A")],
        "RAW": [Node("D1", "1", "K"), Node("U2", "1", "VIN"), Node("U2", "3", "EN")],
        "/LDO_OUT": [
            Node("U2", "5", "VOUT"),
            Node("U1", "1", "IOVDD"),
            Node("C1", "1", ""),
        ],
        "GND": [Node("J1", "A1", "GND"), Node("U2", "2", "GND"), Node("C1", "2", "")],
    }
    return Design(parts, nets)


def _mux_buck_boost(l2_net: str = "/L2") -> Design:
    """A TPS2121 mux U3 picking VBUS (IN1) or a battery on VBAT (IN2) for
    VDD; a TPS63070 buck-boost U5 makes +3V3 from VDD through L1; an MCU U1
    on +3V3."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "J2": Part("J2", "JST", "Connector:Conn_01x02", ""),
        "U3": Part("U3", "TPS2121RUXR", "Power_Management:TPS2121", ""),
        "U5": Part("U5", "TPS63070RNMR", "Regulator_Switching:TPS63070", ""),
        "L1": Part("L1", "1.5uH", "Device:L", ""),
        "U1": Part("U1", "ESP32-S3-WROOM-1", "RF_Module:ESP32-S3-WROOM-1", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("U3", "1", "IN1")],
        "VBAT": [Node("J2", "1", ""), Node("U3", "8", "IN2")],
        "VDD": [Node("U3", "5", "OUT"), Node("U5", "1", "VIN"), Node("U5", "2", "EN")],
        "/L1": [Node("U5", "3", "L1"), Node("L1", "1", "")],
        l2_net: [Node("U5", "4", "L2"), Node("L1", "2", "")],
        "+3V3": [Node("U5", "6", "VOUT"), Node("U1", "2", "3V3")],
        "GND": [Node("J2", "2", ""), Node("U3", "9", "GND"), Node("U5", "7", "GND")],
    }
    return Design(parts, nets)


def _charger_board() -> Design:
    """An MCP73831 charger U1 from VBUS to the cell on +BATT, a slide switch
    SW1 from +BATT to VSYS, a buck U2 (LX through L1) from VSYS to an
    unnamed rail set by a feedback divider, a reset button SW2."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "BT1": Part("BT1", "LiPo", "Device:Battery_Cell", ""),
        "U1": Part("U1", "MCP73831T-2ACI/OT", "Battery_Management:MCP73831", ""),
        "SW1": Part("SW1", "PWR", "Switch:SW_SPDT", ""),
        "U2": Part("U2", "SY8088", "Regulator_Switching:SY8088", ""),
        "L1": Part("L1", "2.2uH", "Device:L", ""),
        "U4": Part("U4", "SENSOR", "Sensor:Thing", ""),
        "SW2": Part("SW2", "RESET", "Switch:SW_Push", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("U1", "4", "VDD")],
        "+BATT": [Node("U1", "3", "VBAT"), Node("BT1", "1", "+"), Node("SW1", "1", "")],
        "/PROG": [Node("U1", "5", "PROG")],
        "VSYS": [Node("SW1", "2", ""), Node("U2", "4", "IN"), Node("U2", "1", "EN")],
        "/LX": [Node("U2", "3", "LX"), Node("L1", "1", "")],
        "/OUT": [Node("L1", "2", ""), Node("U4", "1", "VDD"), Node("SW2", "1", "")],
        "/RST": [Node("SW2", "2", ""), Node("U4", "2", "RST")],
        "GND": [Node("BT1", "2", "-"), Node("U1", "2", "VSS"), Node("U2", "2", "GND")],
    }
    return Design(parts, nets)


def test_ldo_chain_passes_parts_and_reads_a_fixed_output() -> None:
    tree = power.build(_usb_ldo_chain())
    assert [(p.ref, p.kind, p.inputs, p.output) for p in tree.parts] == [
        ("D1", "pass", ["+5V"], "RAW"),
        ("F1", "pass", ["VBUS"], "+5V"),
        ("U2", "regulator", ["RAW"], "/LDO_OUT"),
    ]
    assert [r.net for r in tree.rails] == ["VBUS", "+5V", "RAW", "/LDO_OUT"]
    vbus = _rail(tree, "VBUS")
    assert (vbus.source, vbus.min, vbus.max, vbus.basis) == ("usb", 5.0, 5.0, "usb")
    raw = _rail(tree, "RAW")
    assert (raw.min, raw.basis, raw.fed_by, raw.loads) == (5.0, "upstream", ["D1"], ["U2"])
    assert "diode's drop" in raw.note
    out = _rail(tree, "/LDO_OUT")
    assert (out.min, out.basis, out.loads) == (3.3, "fixed", ["U1"])


def test_mux_takes_its_range_from_both_sources() -> None:
    """The private board's shape: VDD has no name or calculation, so it is
    whatever the TPS2121 picks, VBUS or the battery."""
    tree = power.build(_mux_buck_boost())
    u3 = next(p for p in tree.parts if p.ref == "U3")
    assert (u3.kind, u3.inputs, u3.output) == ("switch", ["VBUS", "VBAT"], "VDD")
    u5 = next(p for p in tree.parts if p.ref == "U5")
    assert (u5.kind, u5.inputs, u5.output) == ("regulator", ["VDD"], "+3V3")
    assert _rail(tree, "VBAT").source == "battery"
    vdd = _rail(tree, "VDD")
    assert (vdd.source, vdd.min, vdd.max, vdd.basis) == (None, 3.0, 5.0, "upstream")
    assert vdd.inferred  # the battery's half is the Li-ion default
    assert vdd.loads == ["U5"]
    v33 = _rail(tree, "+3V3")
    assert (v33.min, v33.basis, v33.fed_by, v33.loads) == (3.3, "named", ["U5"], ["U1"])


def test_a_fuse_from_the_battery_feeds_the_mux_one_way() -> None:
    """A fuse between the battery and the mux's input is oriented once:
    flipped back, it made the battery a rail fed from the mux side and it
    lost its source."""
    d = _mux_buck_boost()
    d.parts["F1"] = Part("F1", "1A", "Device:Fuse", "")
    d.nets["VBAT"] = [Node("J2", "1", ""), Node("F1", "1", "")]
    d.nets["/IN2"] = [Node("F1", "2", ""), Node("U3", "8", "IN2")]
    tree = power.build(Design(d.parts, d.nets))
    assert [(p.ref, p.inputs, p.output) for p in tree.parts if p.ref == "F1"] == [
        ("F1", ["VBAT"], "/IN2")
    ]
    assert _rail(tree, "VBAT").source == "battery"
    assert _rail(tree, "/IN2").fed_by == ["F1"]


def test_buck_boost_output_is_its_vout_pin_not_the_inductor_net() -> None:
    """L2 wired to +3V3 (the bug) adds no edge: the switch-node pins don't
    make a rail when VOUT already names one."""
    tree = power.build(_mux_buck_boost(l2_net="+3V3"))
    assert [p.output for p in tree.parts if p.ref == "U5"] == ["+3V3"]


def test_charger_slide_switch_and_buck_through_its_inductor() -> None:
    calcs = [
        Calculation(
            id="cf1",
            kind="regulator_output",
            ic="U2",
            pins=["U2.5"],
            value=1.8,
            unit="V",
            expected=None,
            flagged=False,
            text="U2 sets 1.8 V",
        )
    ]
    tree = power.build(_charger_board(), calcs)
    edges = {(p.ref, p.kind, tuple(p.inputs), p.output) for p in tree.parts}
    assert edges == {
        ("U1", "charger", ("VBUS",), "+BATT"),
        ("SW1", "pass", ("+BATT",), "VSYS"),
        ("U2", "regulator", ("VSYS",), "/OUT"),
    }
    batt = _rail(tree, "+BATT")
    assert (batt.source, batt.min, batt.max, batt.basis, batt.inferred) == (
        "battery",
        3.0,
        4.2,
        "default",
        True,
    )
    assert batt.fed_by == ["U1"]  # the charger, which doesn't make it a non-source
    vsys = _rail(tree, "VSYS")
    assert (vsys.min, vsys.max, vsys.basis, vsys.inferred) == (3.0, 4.2, "upstream", True)
    out = _rail(tree, "/OUT")
    assert (out.min, out.basis, out.note) == (1.8, "calculated", "U2 feedback (cf1)")
    assert out.loads == ["U4"]
    assert not tree.battery_override


def test_battery_from_the_charger_datasheet_and_from_the_user() -> None:
    lim = LimitFact(
        parameter="Regulated output voltage",
        kind="characteristic",
        pins=[],
        all_pins=False,
        min=4.168,
        typ=4.2,
        max=4.232,
        unit="V",
        conditions="",
        page=3,
    )
    tree = power.build(_charger_board(), limits={"U1": [lim]})
    batt = _rail(tree, "+BATT")
    assert (batt.min, batt.max, batt.basis, batt.inferred) == (3.0, 4.2, "charger", True)
    assert "datasheet p.3" in batt.note

    tree = power.build(_charger_board(), limits={"U1": [lim]}, battery=(3.2, 3.6))
    batt = _rail(tree, "+BATT")
    assert (batt.min, batt.max, batt.basis, batt.inferred) == (3.2, 3.6, "user", False)
    assert _rail(tree, "VSYS").min == 3.2
    assert tree.battery_override


def test_regulator_output_from_its_datasheet() -> None:
    design = _mux_buck_boost()
    design.nets["/V3"] = design.nets.pop("+3V3")
    lim = LimitFact(
        parameter="Output voltage",
        kind="characteristic",
        pins=[],
        all_pins=False,
        min=3.25,
        typ=None,
        max=3.35,
        unit="V",
        conditions="",
        page=5,
    )
    tree = power.build(design, limits={"U5": [lim]})
    v3 = _rail(tree, "/V3")
    assert (v3.min, v3.basis) == (3.3, "datasheet")
    unknown = power.build(design)
    assert _rail(unknown, "/V3").basis == "unknown"
    assert "voltage unknown" in power.format_power(unknown)


def test_op_amp_and_push_button_are_not_power_parts() -> None:
    parts = {
        "U1": Part("U1", "LM358", "Amplifier_Operational:LM358", ""),
        "SW1": Part("SW1", "BOOT", "Switch:SW_Push", ""),
    }
    nets = {
        "+5V": [Node("U1", "8", "V+"), Node("SW1", "1", "")],
        "/IN": [Node("U1", "3", "+"), Node("SW1", "2", "")],
        "/OUT": [Node("U1", "1", "OUT")],
        "GND": [Node("U1", "4", "V-")],
    }
    tree = power.build(Design(parts, nets))
    assert tree.parts == []
    assert [r.net for r in tree.rails] == []


def test_format_power_lists_parts_then_rails() -> None:
    text = power.format_power(power.build(_mux_buck_boost()))
    assert "- U3 TPS2121RUXR (power mux/switch): VBUS, VBAT -> VDD" in text
    assert "- VDD: 3-5 V (upstream, VBAT via U3, VBUS via U3; inferred, check it)" in text
    assert "- VBUS [source: usb]: 5 V" in text
    assert power.format_power(None).startswith("(no power rails found")


def test_prompt_carries_the_tree() -> None:
    tree = power.build(_mux_buck_boost())
    user = prompt.build_user_message(
        symptoms=[],
        measurements=[],
        netlist="",
        erc="",
        pin_table="",
        findings="",
        power=power.format_power(tree),
    )
    assert "## Power tree (built from the netlist; inferred voltages marked)\n" in user
    summary = DesignSummary(
        mode="review",
        project="p",
        sheets=1,
        parts=6,
        nets=7,
        ics=[],
        erc=[],
        wiring=[],
        pin_warnings=[],
        references=[],
        calculations=[],
        symptoms=[],
        measurements=[],
        power=tree,
    )
    ask = prompt.build_result_ask_message(
        summary,
        CheckedAnalysis(hypotheses=[], open_questions=[], glossary=[], problems=[]),
        [],
        "q",
    )
    assert "## Power tree (built from the netlist)\n" in ask


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("  ", None),
        ("3.7", (3.7, 3.7)),
        ("3.0-4.2", (3.0, 4.2)),
        ("3.0 – 4.2 V", (3.0, 4.2)),
        ("6~8.4V", (6.0, 8.4)),
        ("2.5 to 3.6", (2.5, 3.6)),
    ],
)
def test_parse_battery(text: str, expected: tuple[float, float] | None) -> None:
    assert power.parse_battery(text) == expected


@pytest.mark.parametrize("text", ["abc", "4.2-3.0", "0", "-1", "3-4-5", "100"])
def test_parse_battery_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        power.parse_battery(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AMS1117-3.3", 3.3),
        ("AMS1117-3V3", 3.3),
        ("RT9013-33GB", 3.3),
        ("LM1117-5.0", 5.0),
        ("XC6206P332MR", None),
        ("TPS63070RNMR", None),
        ("AMS1117-ADJ", None),
    ],
)
def test_fixed_voltage(value: str, expected: float | None) -> None:
    assert power.fixed_voltage(value) == expected
