"""Connector signal lines that reach an IC pin with no ESD part on them
(``esd.check``). Hand-built designs and power trees; pure functions.

The board: J1, a USB-C receptacle whose D+/D- go to U1 (a microcontroller)
on pins 47/46; +3V3 is the one rail. Tests add parts on those nets.
"""

from app.features.debug import esd
from app.features.debug.design import Design, Node, Part
from app.features.debug.facts import Fact
from app.features.debug.schemas import PowerRail, PowerTree

_LIBS = {
    "J1": "Connector:USB_C_Receptacle_USB2.0",
    "J2": "Connector_Generic:Conn_01x04",
    "TP1": "Connector:TestPoint",
    "U1": "MCU_RaspberryPi:RP2040",
    "U2": "Power_Protection:USBLC6-2SC6",
    "D1": "Device:D_TVS",
    "D2": "Device:LED",
}


def _board(nets: dict[str, list[tuple[str, str, str]]]) -> tuple[Design, PowerTree]:
    """A design from {net: [(ref, pin, function)]}; +3V3 is the one rail."""
    refs = {ref for nodes in nets.values() for ref, _, _ in nodes}
    design = Design(
        {r: Part(r, r, _LIBS.get(r, "Device:R" if r[0] == "R" else "x:IC"), "") for r in refs},
        {net: [Node(*n) for n in nodes] for net, nodes in nets.items()},
    )
    rail = PowerRail(
        net="+3V3",
        source=None,
        min=3.3,
        max=3.3,
        basis="named",
        inferred=False,
        note="",
        fed_by=[],
        loads=[],
    )
    return design, PowerTree(rails=[rail], parts=[])


def _usb(**extra: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str, str]]]:
    """J1's D+ and D- straight to U1, plus VBUS and ground; ``extra`` adds
    nodes to a net by name (``USB_DP=[...]``) or adds a new net."""
    nets = {
        "USB_DP": [("J1", "A6", "D+"), ("J1", "B6", "D+"), ("U1", "47", "USB_DP")],
        "USB_DM": [("J1", "A7", "D-"), ("J1", "B7", "D-"), ("U1", "46", "USB_DM")],
        "VBUS": [("J1", "A4", "VBUS")],
        "GND": [("J1", "A1", "GND"), ("U1", "19", "GND")],
        "+3V3": [("U1", "1", "IOVDD")],
    }
    for net, nodes in extra.items():
        nets[net] = nets.get(net, []) + nodes
    return nets


def test_unprotected_usb_lines_are_one_may_fail_calculation():
    calcs = esd.check(*_board(_usb()), first_id=7)
    assert len(calcs) == 1
    c = calcs[0]
    assert (c.id, c.kind, c.ic, c.flagged, c.value) == ("cf7", "connector_esd", "J1", False, None)
    assert "a USB connector" in c.text and "May fail" in c.text
    assert "U1.47(USB_DP)" in c.text and "U1.46(USB_DM)" in c.text
    # USB-C's A6 and B6 are one line, cited once.
    assert c.pins == ["J1.A6", "J1.A7", "U1.47", "U1.46"]
    assert "VBUS" not in c.text and "GND" not in c.text


def test_an_esd_array_protects_its_lines():
    nets = _usb(
        USB_DP=[("U2", "1", "I/O1")], USB_DM=[("U2", "3", "I/O2")], GND=[("U2", "2", "GND")]
    )
    assert esd.check(*_board(nets)) == []


def test_a_tvs_diode_to_ground_protects_its_line_only():
    nets = _usb(USB_DP=[("D1", "1", "A1")], GND=[("D1", "2", "A2")])
    [c] = esd.check(*_board(nets))
    assert "U1.46(USB_DM)" in c.text
    assert "U1.47" not in c.text


def test_an_led_is_not_protection():
    nets = _usb(USB_DP=[("D2", "1", "A")], GND=[("D2", "2", "K")])
    [c] = esd.check(*_board(nets))
    assert "U1.47(USB_DP)" in c.text


def test_a_series_resistor_is_followed_but_does_not_protect():
    nets = _usb()
    nets["USB_DP"] = [("J1", "A6", "D+"), ("R1", "1", "")]
    nets["DP_MCU"] = [("R1", "2", ""), ("U1", "47", "USB_DP")]
    [c] = esd.check(*_board(nets))
    assert "U1.47(USB_DP) through R1" in c.text


def test_protection_behind_the_series_resistor_still_counts():
    nets = _usb(GND=[("U2", "2", "GND")])
    nets["USB_DP"] = [("J1", "A6", "D+"), ("R1", "1", "")]
    nets["DP_MCU"] = [("R1", "2", ""), ("U1", "47", "USB_DP"), ("U2", "1", "I/O1")]
    nets["USB_DM"] += [("U2", "3", "I/O2")]
    assert esd.check(*_board(nets)) == []


def test_a_pull_up_is_not_a_path_to_the_rail():
    # R1 pulls D+ to +3V3; the walk must not cross it to U1's IOVDD pin.
    nets = _usb(USB_DP=[("R1", "1", "")], **{"+3V3": [("R1", "2", "")]})
    [c] = esd.check(*_board(nets))
    assert "IOVDD" not in c.text and "through R1" not in c.text


def test_a_header_is_context_and_power_pins_are_ignored():
    nets = {
        "SWDIO": [("J2", "1", "Pin_1"), ("U1", "25", "SWDIO")],
        "+3V3": [("J2", "2", "Pin_2"), ("U1", "1", "IOVDD")],
        "GND": [("J2", "3", "Pin_3"), ("U1", "19", "GND")],
        "NC": [("J2", "4", "Pin_4")],
    }
    [c] = esd.check(*_board(nets))
    assert c.ic == "J2" and not c.flagged
    assert "header or internal connector" in c.text and "Context, not a finding" in c.text
    assert "U1.25(SWDIO)" in c.text
    assert "IOVDD" not in c.text and "Pin_3" not in c.text and "Pin_4" not in c.text


def test_external_connectors_come_before_headers():
    nets = _usb(SWDIO=[("J2", "1", "Pin_1"), ("U1", "25", "SWDIO")])
    calcs = esd.check(*_board(nets))
    assert [c.ic for c in calcs] == ["J1", "J2"]
    assert [c.id for c in calcs] == ["cf1", "cf2"]


def test_a_test_point_is_not_a_connector():
    nets = {"SWCLK": [("TP1", "1", "1"), ("U1", "24", "SWCLK")]}
    assert esd.check(*_board(nets)) == []


def test_the_chips_own_esd_rating_is_quoted_not_trusted():
    facts = [
        Fact("ds2", "U1", 12, "ESD HBM: 2 kV"),
        Fact("ds3", "U1", 10, "IOVDD: max 3.63 V"),
    ]
    [c] = esd.check(*_board(_usb()), facts)
    assert "U1: ESD HBM: 2 kV (ds2)" in c.text
    assert "covers handling" in c.text
    assert "IOVDD: max" not in c.text


def test_connector_kind_reads_symbol_value_and_footprint():
    def kind(ref: str, lib: str, value: str = "", footprint: str = "") -> str | None:
        design = Design({ref: Part(ref, value, lib, footprint)}, {})
        return esd.connector_kind(design, ref)

    assert kind("USB1", "x:Conn") == "USB"
    assert kind("J3", "Connector:RJ45_Shielded") == "RJ45"
    assert kind("J4", "Connector:Conn_Coaxial", footprint="Connector_Coaxial:SMA_Amphenol") == "RF"
    assert kind("J5", "Connector:Barrel_Jack") == "barrel"
    assert kind("J6", "Connector:Micro_SD_Card") == "card"
    assert kind("J7", "Connector_Generic:Conn_01x04", "SMAJ") is None
    assert kind("J8", "Connector_Generic:Conn_02x05", "SWD") is None
