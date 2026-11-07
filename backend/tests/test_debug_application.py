"""The board checked against each datasheet's typical application: missing
parts, values outside the datasheet's range, open pins, and inductors whose
value field disagrees with their part number. Pure functions; no model."""

import pytest

from app.features.debug import application, prompt
from app.features.debug.application import parse_henries
from app.features.debug.design import Design, Node, Part
from app.features.debug.evidence import EvidenceContext, check_claim
from app.features.debug.schemas import AppPart, AppPartKind, Cite, Claim


def _board() -> Design:
    """A buck-boost U1 (pins 1 VIN, 2 L1, 3 L2, 4 VOUT, 5 GND, 6 EN, 7 PG,
    8 FB) whose inductor's value field says 0.47 µH but whose part number
    codes 2.2 µH, two 10 µF output capacitors, EN tied to VIN, PG open, FB
    on a divider, and no input capacitor."""
    parts = {
        "U1": Part("U1", "TPS63802", "Regulator:TPS63802", "VSON-10"),
        "L1": Part("L1", "0.47uH", "DFE201612E-2R2M_P2:DFE201612E-2R2M=P2", "L_0806"),
        "C1": Part("C1", "10uF", "Device:C", "C_0603"),
        "C2": Part("C2", "10uF", "Device:C", "C_0603"),
        "R1": Part("R1", "1M", "Device:R", "R_0402"),
        "R2": Part("R2", "220k", "Device:R", "R_0402"),
        "J1": Part("J1", "Conn", "Connector:Conn_01x02", ""),
    }
    nets = {
        "/VIN": [Node("U1", "1", "VIN"), Node("U1", "6", "EN"), Node("J1", "1", "")],
        "/SW1": [Node("U1", "2", "L1"), Node("L1", "1", "")],
        "/SW2": [Node("U1", "3", "L2"), Node("L1", "2", "")],
        "+3V3": [
            Node("U1", "4", "VOUT"),
            Node("C1", "1", ""),
            Node("C2", "1", ""),
            Node("R1", "1", ""),
        ],
        "GND": [
            Node("U1", "5", "GND"),
            Node("C1", "2", ""),
            Node("C2", "2", ""),
            Node("R2", "2", ""),
            Node("J1", "2", ""),
        ],
        "/PG": [Node("U1", "7", "PG")],
        "/FB": [Node("U1", "8", "FB"), Node("R1", "2", ""), Node("R2", "1", "")],
    }
    return Design(parts, nets)


def _part(
    role: str,
    kind: AppPartKind,
    pins: list[str],
    *,
    value: str = "",
    lo: float | None = None,
    hi: float | None = None,
    unit: str = "",
    required: bool = True,
) -> AppPart:
    ranged = lo is not None or hi is not None
    return AppPart(
        role=role,
        kind=kind,
        value=value,
        pins=pins,
        page=20,
        min=lo,
        max=hi,
        unit=unit,
        range_page=22 if ranged else None,
        required=required,
    )


def _check(*parts: AppPart) -> list[application.Deviation]:
    return application.check(_board(), {"U1": list(parts)})


# --- missing parts ------------------------------------------------------------------


def test_a_required_part_the_board_lacks_is_missing():
    [dv] = _check(_part("input capacitor", "capacitor", ["1", "5"], value="10 µF"))
    assert (dv.id, dv.ref, dv.page, dv.parts) == ("dv1", "U1", 20, ())
    assert dv.text == (
        "U1 (TPS63802): no capacitor joins U1.1 (VIN) on /VIN and U1.5 (GND) on GND, but the "
        "datasheet's typical application has an input capacitor (capacitor 10 µF) there (p.20) "
        "and doesn't call it optional."
    )


def test_an_optional_part_the_board_lacks_is_not_a_deviation():
    assert _check(_part("input capacitor", "capacitor", ["1", "5"], required=False)) == []


def test_a_pull_resistor_is_not_missing_when_its_pin_is_tied_to_a_rail():
    pull_up = _part("EN pull-up resistor", "resistor", ["6"])
    assert _check(pull_up) == []
    # The same pin on a net that isn't a rail needs the resistor.
    nets = dict(_board().nets)
    nets["/VIN"] = [n for n in nets["/VIN"] if n.pin != "6"]
    nets["/EN"] = [Node("U1", "6", "EN"), Node("J1", "3", "")]
    [missing] = application.check(Design(_board().parts, nets), {"U1": [pull_up]})
    assert (
        "no resistor joins U1.6 (EN) on /EN, but the datasheet's typical application has an EN"
        in (missing.text)
    )


def test_any_part_of_the_kind_on_a_single_pins_net_is_present():
    # R1 is on +3V3, so a resistor on VOUT counts as there, whatever it does.
    assert _check(_part("output load resistor", "resistor", ["4"])) == []


def test_a_part_reaching_a_pin_through_one_series_resistor_is_present():
    # An MCU's crystal: XIN direct, XOUT through a 1 kΩ series resistor.
    parts = {
        "U1": Part("U1", "RP2040", "MCU:RP2040", "QFN-56"),
        "Y1": Part("Y1", "12MHz", "Device:Crystal", ""),
        "R3": Part("R3", "1k", "Device:R", ""),
        "R4": Part("R4", "1k", "Device:R", ""),
    }
    nets = {
        "/XIN": [Node("U1", "20", "XIN"), Node("Y1", "1", "")],
        "/XOUT": [Node("U1", "21", "XOUT"), Node("R3", "1", "")],
        "/XO": [Node("R3", "2", ""), Node("Y1", "2", "")],
        "/GPIO0": [Node("U1", "2", "GPIO0"), Node("R4", "1", "")],
        "/LED": [Node("R4", "2", "")],
    }
    board = Design(parts, nets)
    crystal = _part("crystal", "crystal", ["20", "21"])
    assert application.check(board, {"U1": [crystal]}) == []
    # A resistor role doesn't hop: R4 doesn't stand in for a resistor between
    # GPIO0 and XIN via R3's nets.
    [dv] = application.check(board, {"U1": [_part("strap resistor", "resistor", ["2", "20"])]})
    assert "no resistor joins U1.2 (GPIO0) on /GPIO0 and U1.20 (XIN) on /XIN" in dv.text


def test_kind_other_and_parts_spanning_three_nets_are_not_checked():
    assert _check(_part("ferrite bead", "other", ["1", "5"])) == []
    assert _check(_part("snubber", "capacitor", ["1", "2", "5"])) == []


# --- open pins ----------------------------------------------------------------------


def test_a_pin_the_datasheet_ties_to_a_required_part_left_open_is_one_deviation():
    pull_up = _part("PG pull-up resistor", "resistor", ["7"])
    [dv] = _check(pull_up, _part("PG filter capacitor", "capacitor", ["7"]))
    assert dv.text == (
        "U1 (TPS63802): U1.7 (PG) connects to nothing, but the datasheet's typical application "
        "ties it to a PG pull-up resistor (resistor) (p.20)."
    )
    assert _check(_part("PG pull-up resistor", "resistor", ["7"], required=False)) == []


# --- values -------------------------------------------------------------------------


def test_an_inductor_is_checked_by_its_part_number_and_a_disagreeing_value_is_reported():
    mismatch, out = _check(_part("inductor", "inductor", ["2", "3"], lo=0.33, hi=1, unit="µH"))
    assert mismatch.parts == ("L1",) and mismatch.page == 20
    assert mismatch.text == (
        "L1's value field says 0.47 µH but its part number (DFE201612E-2R2M=P2) codes 2.2 µH; "
        "the fitted part is the part number's. It is U1 (TPS63802)'s inductor (p.20)."
    )
    assert out.page == 22
    assert out.text == (
        "U1 (TPS63802): inductor L1 2.2 µH is outside the datasheet's 0.33–1 µH (p.22)."
    )


def test_an_inductor_within_range_by_its_part_number_is_only_the_mismatch():
    [dv] = _check(_part("inductor", "inductor", ["2", "3"], lo=1, hi=2.2, unit="µH"))
    assert "value field says 0.47 µH" in dv.text


def test_a_typical_value_alone_is_never_flagged():
    assert _check(_part("feedback resistor", "resistor", ["8", "5"], value="10k")) == []


def test_a_resistor_outside_its_range():
    [dv] = _check(_part("feedback resistor", "resistor", ["8", "5"], lo=10, hi=100, unit="kΩ"))
    assert dv.parts == ("R2",)
    assert dv.text == (
        "U1 (TPS63802): feedback resistor R2 220 kΩ is outside the datasheet's 10–100 kΩ (p.22)."
    )
    assert _check(_part("feedback resistor", "resistor", ["8", "5"], hi=500, unit="kohm")) == []


def test_parallel_capacitors_are_summed_and_only_a_low_total_counts():
    assert _check(_part("output capacitor", "capacitor", ["4", "5"], lo=16, unit="µF")) == []
    assert _check(_part("output capacitor", "capacitor", ["4", "5"], lo=1, hi=4.7, unit="µF")) == []
    [dv] = _check(_part("output capacitor", "capacitor", ["4", "5"], lo=22, unit="µF"))
    assert dv.parts == ("C1", "C2")
    assert "output capacitor C1 + C2 is 20 µF nominal, below the datasheet's minimum of 22 µF" in (
        dv.text
    )


def test_a_unit_that_does_not_fit_the_kind_skips_the_value_check():
    assert _check(_part("output capacitor", "capacitor", ["4", "5"], lo=22, unit="mΩ")) == []


# --- numbering, prompt and citing ---------------------------------------------------


def test_deviations_are_numbered_in_order_and_formatted_for_the_prompt():
    found = _check(
        _part("input capacitor", "capacitor", ["1", "5"]),
        _part("PG pull-up resistor", "resistor", ["7"]),
    )
    assert [d.id for d in found] == ["dv1", "dv2"]
    text = application.format_deviations(found)
    assert (
        text.startswith("dv1: U1 (TPS63802): no capacitor") and "\ndv2: U1 (TPS63802): U1.7" in text
    )
    assert application.format_deviations([]).startswith("(none:")
    assert application.check(_board(), {"U9": [_part("x", "capacitor", ["1"])]}) == []


def test_deviations_section_only_when_given():
    common = {
        "symptoms": [],
        "measurements": [],
        "netlist": "n",
        "erc": "e",
        "pin_table": "p",
        "findings": "f",
    }
    assert "## Deviations" not in prompt.build_user_message(**common)
    message = prompt.build_user_message(**common, datasheet_facts="U1…\n", deviations="dv1: x\n")
    assert (
        message.index("## Datasheet facts")
        < message.index("## Deviations")
        < message.index("## Verified findings")
    )


def test_a_deviation_backs_a_datasheet_claim():
    ctx = EvidenceContext(
        design=_board(),
        erc={},
        pin_data=frozenset(),
        symptom_ids=frozenset(),
        measurement_ids=frozenset(),
        datasheet_ids=frozenset({"ds1", "dv1"}),
    )
    claim = Claim(
        text="The input capacitor of the typical application is missing.",
        evidence="datasheet",
        cites=[Cite(kind="datasheet", pin=None, net=None, id="dv1")],
    )
    assert check_claim(claim, ctx).evidence == "datasheet"


# --- reading values -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "henries"),
    [
        ("0.47uH", 0.47e-6),
        ("2u2", 2.2e-6),
        ("470nH", 470e-9),
        ("2.2 µH 3A", 2.2e-6),
        ("10mH", 10e-3),
        ("10uF", None),
        ("DFE201612E", None),
    ],
)
def test_parse_henries(text: str, henries: float | None):
    got = parse_henries(text)
    assert got == pytest.approx(henries) if henries is not None else got is None


@pytest.mark.parametrize(
    ("mpn", "lib_part", "henries"),
    [
        ("", "DFE201612E-2R2M_P2:DFE201612E-2R2M=P2", 2.2e-6),
        ("DFE252012F-R47M=P2", "Device:L", 0.47e-6),
        ("NR3015T1R0N", "Device:L", 1.0e-6),
        ("XAL4020-222MEB", "Device:L", None),  # a plain 3-digit code is ignored
        ("SRR1260-100M", "Device:L", None),
        ("LQH3NPN2R2MMEL", "Inductor:L_1R5", None),  # two codes that disagree
    ],
)
def test_inductor_code_from_the_part_number(mpn: str, lib_part: str, henries: float | None):
    code = application._inductor_code(Part("L1", "", lib_part, "", mpn=mpn))
    assert code == pytest.approx(henries) if henries is not None else code is None
