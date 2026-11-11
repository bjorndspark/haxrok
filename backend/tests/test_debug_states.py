"""Operating states (``states.walk``, through ``power.build``): which rails
come up from which sources, which parts their enables hold off, and where a
rail that's down is fed backwards. Hand-built designs; pure functions."""

from app.features.debug import power
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import OperatingState, PowerTree


def _state(tree: PowerTree, *sources: str) -> OperatingState:
    return next(s for s in tree.states if set(s.sources) == set(sources))


def _board(en_net: str = "VBUS", en_name: str = "EN", *extra: tuple[str, Node]) -> Design:
    """A TPS2121 mux U3 picking VBUS or VBAT for VDD, a TPS63070 U5 (EN on
    VDD) making +3V3 for an ESP32-S3 U1, and an AP2112K LDO U2 (its enable
    on ``en_net``) making +3V3_USB from VBUS alone for a sensor U6. U1 and
    U6 share I2C on /SDA; R17 pulls /SCL, U1's other I2C line, up to VBUS."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "J2": Part("J2", "JST", "Connector:Conn_01x02", ""),
        "U3": Part("U3", "TPS2121RUXR", "Power_Management:TPS2121", ""),
        "U5": Part("U5", "TPS63070RNMR", "Regulator_Switching:TPS63070", ""),
        "L1": Part("L1", "1.5uH", "Device:L", ""),
        "U1": Part("U1", "ESP32-S3-WROOM-1", "RF_Module:ESP32-S3-WROOM-1", ""),
        "U2": Part("U2", "AP2112K-3.3", "Regulator_Linear:AP2112K-3.3", ""),
        "U6": Part("U6", "BME280", "Sensor:BME280", ""),
        "R17": Part("R17", "4.7k", "Device:R", ""),
    }
    nets: dict[str, list[Node]] = {
        "VBUS": [
            Node("J1", "A4", "VBUS"),
            Node("U3", "1", "IN1"),
            Node("U2", "1", "VIN"),
            Node("R17", "2", ""),
        ],
        "VBAT": [Node("J2", "1", ""), Node("U3", "8", "IN2")],
        "VDD": [Node("U3", "5", "OUT"), Node("U5", "1", "VIN"), Node("U5", "2", "EN")],
        "/L1": [Node("U5", "3", "L1"), Node("L1", "1", "")],
        "/L2": [Node("U5", "4", "L2"), Node("L1", "2", "")],
        "+3V3": [Node("U5", "6", "VOUT"), Node("U1", "2", "3V3")],
        "+3V3_USB": [Node("U2", "5", "VOUT"), Node("U6", "1", "VDD")],
        "/SDA": [Node("U1", "8", "SDA"), Node("U6", "4", "SDA")],
        "/SCL": [Node("U1", "9", "SCL"), Node("R17", "1", "")],
        "GND": [Node("J2", "2", ""), Node("U3", "9", "GND"), Node("U5", "7", "GND")],
    }
    nets["GND"].append(Node("U2", "2", "GND"))
    nets.setdefault(en_net, []).append(Node("U2", "3", en_name))
    for net, node in extra:
        nets.setdefault(net, []).append(node)
    return Design(parts, nets)


def test_states_cover_each_source_combination() -> None:
    tree = power.build(_board())
    assert [s.sources for s in tree.states] == [["VBAT"], ["VBUS"], ["VBAT", "VBUS"]]
    both = _state(tree, "VBUS", "VBAT")
    assert set(both.up) == {"VBUS", "VBAT", "VDD", "+3V3", "+3V3_USB"}
    assert both.down == [] and both.back_powered == []


def test_a_rail_made_from_usb_alone_is_down_on_battery() -> None:
    battery = _state(power.build(_board()), "VBAT")
    assert set(battery.up) == {"VBAT", "VDD", "+3V3"}
    assert set(battery.down) == {"VBUS", "+3V3_USB"}


def test_back_power_through_an_esd_diode_and_a_pull_up_on_battery() -> None:
    battery = _state(power.build(_board()), "VBAT")
    found = {(b.rail, tuple(b.via)) for b in battery.back_powered}
    # U6 is unpowered, with its SDA on a line U1 drives: its ESD diode.
    assert ("+3V3_USB", ("U6.4",)) in found
    # R17 pulls SCL up to VBUS, which is down: U1 feeds VBUS through it.
    assert ("VBUS", ("R17", "U1.9")) in found
    assert len(found) == 2
    # On USB alone nothing's down, so nothing's back-powered.
    assert _state(power.build(_board()), "VBUS").back_powered == []


def test_an_enable_pulled_to_ground_holds_its_regulator_off() -> None:
    tree = power.build(_board("GND"))
    usb = _state(tree, "VBUS")
    assert "+3V3_USB" in usb.down
    assert usb.off == ["U2 held off: U2.3 (EN) is tied to ground"]


def test_an_active_low_enable_runs_low_and_a_floating_one_is_noted() -> None:
    low = power.build(_board("GND", "~{EN}"))
    assert "+3V3_USB" in _state(low, "VBUS").up
    floating = power.build(_board("/EN"))
    usb = _state(floating, "VBUS")
    assert "+3V3_USB" in usb.up
    assert usb.off == [
        "U2 taken as on: U2.3 (EN) has nothing driving it; check whether it has an internal pull"
    ]


def test_an_enable_driven_by_a_powered_ic_follows_it() -> None:
    tree = power.build(_board("/LDO_EN", "EN", ("/LDO_EN", Node("U1", "12", "IO4"))))
    assert "+3V3_USB" in _state(tree, "VBUS").up  # U1 is on +3V3, up from VBUS
    assert _state(tree, "VBUS").off == []


def test_a_slide_switch_feeds_the_battery_backwards_from_usb() -> None:
    """VSYS from the cell through SW1 or from VBUS through D1: on USB alone
    VSYS is up, and SW1 conducts both ways into +BATT."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "BT1": Part("BT1", "LiPo", "Device:Battery_Cell", ""),
        "U1": Part("U1", "MCP73831T-2ACI/OT", "Battery_Management:MCP73831", ""),
        "SW1": Part("SW1", "PWR", "Switch:SW_SPDT", ""),
        "D1": Part("D1", "SS14", "Device:D_Schottky", ""),
        "U4": Part("U4", "SENSOR", "Sensor:Thing", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("U1", "4", "VDD"), Node("D1", "2", "A")],
        "+BATT": [Node("U1", "3", "VBAT"), Node("BT1", "1", "+"), Node("SW1", "1", "")],
        "VSYS": [Node("SW1", "2", ""), Node("D1", "1", "K"), Node("U4", "1", "VDD")],
        "GND": [Node("BT1", "2", "-"), Node("U1", "2", "VSS")],
    }
    tree = power.build(Design(parts, nets))
    usb = _state(tree, "VBUS")
    assert "VSYS" in usb.up and "+BATT" in usb.down
    assert [(b.rail, b.via) for b in usb.back_powered] == [("+BATT", ["SW1"])]
    # The battery alone: VSYS up through SW1, VBUS down; D1 blocks it.
    assert _state(tree, "+BATT").back_powered == []


def test_the_prompt_lists_the_states() -> None:
    text = power.format_power(power.build(_board()))
    assert "Operating states (which rails are up from which sources):" in text
    assert "- VBAT only: up VBAT, VDD, +3V3; down VBUS, +3V3_USB" in text
    assert "  - may fail, not verified: R17 pulls SCL up to VBUS, which is down" in text
    assert "U6's supply +3V3_USB is down, but its pins are driven: U6.4 on SDA (from U1.8)" in text


def test_a_load_sharing_fet_carries_the_battery_and_blocks_usb() -> None:
    """Q1, a P-FET from the cell on +BATT (drain) to +5V (source), its gate
    on VBUS; D4 from VBUS to +5V. Each source alone brings +5V up, and
    neither feeds the other backwards."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "U6": Part("U6", "MCP73831T-2ACI/OT", "Battery_Management:MCP73831", ""),
        "Q1": Part("Q1", "DMG3415U", "Transistor_FET:DMG2301L", ""),
        "D4": Part("D4", "B5819W", "Device:D_Schottky", ""),
        "U5": Part("U5", "SY8088", "Regulator_Switching:SY8088", ""),
    }
    nets = {
        "VBUS": [
            Node("J1", "A4", "VBUS"),
            Node("U6", "4", "VDD"),
            Node("D4", "2", "A"),
            Node("Q1", "1", "G"),
        ],
        "+BATT": [Node("U6", "3", "VBAT"), Node("Q1", "3", "D")],
        "+5V": [Node("Q1", "2", "S"), Node("D4", "1", "K"), Node("U5", "4", "IN")],
        "+3V3": [Node("U5", "5", "OUT")],
        "GND": [Node("U6", "2", "VSS")],
    }
    tree = power.build(Design(parts, nets))
    q1 = next(p for p in tree.parts if p.ref == "Q1")
    assert (q1.kind, q1.inputs, q1.output) == ("pass", ["+BATT"], "+5V")
    battery = _state(tree, "+BATT")
    assert set(battery.up) == {"+BATT", "+5V", "+3V3"} and battery.back_powered == []
    assert _state(tree, "VBUS").back_powered == []
