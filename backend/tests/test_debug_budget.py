"""Each rail's current budget (``budget.apply``): its parts' maximum supply
currents against the rated current of the part making it. Hand-built
designs through ``power.build``; pure functions, no model."""

from collections.abc import Sequence

from app.features.debug import budget, power
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import Calculation, LimitFact, PowerRail, PowerTree


def _limit(
    parameter: str,
    max_: float | None,
    unit: str = "mA",
    pins: tuple[str, ...] = (),
    typ: float | None = None,
    page: int = 1,
) -> LimitFact:
    return LimitFact(
        parameter=parameter,
        kind="characteristic",
        pins=list(pins),
        all_pins=False,
        min=None,
        typ=typ,
        max=max_,
        unit=unit,
        conditions="",
        page=page,
    )


def _board() -> Design:
    """A TPS2121 mux U3 picking VBUS or a battery on VBAT for VDD, a
    TPS63070 buck-boost U5 making +3V3 from VDD, an ESP32-S3 U1 and an
    amplifier U4 on +3V3."""
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "J2": Part("J2", "JST", "Connector:Conn_01x02", ""),
        "U3": Part("U3", "TPS2121RUXR", "Power_Management:TPS2121", ""),
        "U5": Part("U5", "TPS63070RNMR", "Regulator_Switching:TPS63070", ""),
        "L1": Part("L1", "1.5uH", "Device:L", ""),
        "U1": Part("U1", "ESP32-S3-WROOM-1", "RF_Module:ESP32-S3-WROOM-1", ""),
        "U4": Part("U4", "MAX98357A", "Audio:MAX98357A", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("U3", "1", "IN1")],
        "VBAT": [Node("J2", "1", ""), Node("U3", "8", "IN2")],
        "VDD": [Node("U3", "5", "OUT"), Node("U5", "1", "VIN"), Node("U5", "2", "EN")],
        "/L1": [Node("U5", "3", "L1"), Node("L1", "1", "")],
        "/L2": [Node("U5", "4", "L2"), Node("L1", "2", "")],
        "+3V3": [Node("U5", "6", "VOUT"), Node("U1", "2", "3V3"), Node("U4", "7", "VDD")],
        "GND": [Node("J2", "2", ""), Node("U3", "9", "GND"), Node("U5", "7", "GND")],
    }
    return Design(parts, nets)


def _limits(rated_ma: float = 2000) -> dict[str, list[LimitFact]]:
    return {
        "U1": [
            _limit("Supply current, Wi-Fi TX", 500, pins=("2",), page=27),
            _limit("Supply current, deep sleep", 0.008, page=27),
        ],
        "U5": [_limit("Output current", rated_ma, page=1)],
        "U3": [_limit("Output current", 4.5, unit="A", page=5)],
    }


def _apply(
    design: Design, limits: dict[str, list[LimitFact]], calcs: Sequence[Calculation] = ()
) -> PowerTree:
    return budget.apply(design, power.build(design, calcs, limits), limits, calcs)


def _rail(tree: PowerTree, net: str) -> PowerRail:
    return next(r for r in tree.rails if r.net == net)


def test_rail_load_against_its_regulator_with_unknown_parts() -> None:
    tree = _apply(_board(), _limits())
    b = _rail(tree, "+3V3").budget
    assert b is not None
    assert b.load_ma == 500  # the max active figure, not the sleep one
    assert b.unknown == ["U4"]
    assert (b.rated_ma, b.rated_note, b.over) == (2000, "U5 datasheet p.1", False)
    u1 = next(x for x in b.loads if x.ref == "U1")
    assert u1.note == "datasheet p.27"


def test_upstream_rails_carry_the_load_stepped_up_and_the_unknowns() -> None:
    tree = _apply(_board(), _limits())
    vdd = _rail(tree, "VDD")
    assert (vdd.min, vdd.max) == (3.0, 5.0)
    b = vdd.budget
    assert b is not None
    # U5 makes 3.3 V from as little as 3.0 V: 500 mA x 1.1
    assert b.load_ma == 550
    assert b.unknown == ["U4"]
    assert b.loads[0].note == "+3V3's load ×1.10, stepped up"
    assert (b.rated_ma, b.rated_note) == (4500, "U3 datasheet p.5")
    # a mux counts its whole load on each input
    for net in ("VBUS", "VBAT"):
        up = _rail(tree, net).budget
        assert up is not None
        assert (up.load_ma, up.unknown, up.rated_ma) == (550, ["U4"], None)


def test_over_the_rating_is_may_fail_in_the_prompt() -> None:
    tree = _apply(_board(), _limits(rated_ma=300))
    rail = _rail(tree, "+3V3")
    assert rail.budget is not None and rail.budget.over
    line = next(x for x in power.format_power(tree).splitlines() if x.startswith("- +3V3"))
    assert line.endswith(
        "load at least 500 mA (1 part unknown: U4) against 300 mA rated (U5 datasheet p.1); "
        "OVER BUDGET: may fail, not verified, peaks rarely coincide"
    )


def test_known_load_reads_without_at_least() -> None:
    limits = _limits()
    limits["U4"] = [_limit("Quiescent current", None, typ=2.4, page=3)]
    tree = _apply(_board(), limits)
    b = _rail(tree, "+3V3").budget
    assert b is not None
    assert (b.load_ma, b.unknown) == (502.4, [])
    assert next(x for x in b.loads if x.ref == "U4").note == "datasheet p.3, typical"
    assert "load 502.4 mA against 2000 mA rated" in power.format_power(tree)


def test_figures_split_by_supply_pin_and_whole_part_on_each_rail() -> None:
    """An MCU U1 with VDD (pin 1) on +3V3 and VDDA (pin 9) on +3V3A, both
    from fixed LDOs on VIN."""
    parts = {
        "U2": Part("U2", "AMS1117-3.3", "Regulator_Linear:AMS1117-3.3", ""),
        "U6": Part("U6", "AP2112K-3.3", "Regulator_Linear:AP2112K-3.3", ""),
        "U1": Part("U1", "STM32F030C8T6", "MCU_ST_STM32F0:STM32F030C8Tx", ""),
        "J1": Part("J1", "Conn", "Connector:Conn_01x02", ""),
    }
    nets = {
        "VIN": [Node("J1", "1", ""), Node("U2", "3", "VI"), Node("U6", "1", "VIN")],
        "+3V3": [Node("U2", "2", "VO"), Node("U1", "1", "VDD")],
        "+3V3A": [Node("U6", "5", "VOUT"), Node("U1", "9", "VDDA")],
        "GND": [Node("J1", "2", ""), Node("U2", "1", "GND")],
    }
    design = Design(parts, nets)
    split = {"U1": [_limit("IDD", 20, pins=("1",)), _limit("IDDA", 0.2, pins=("9",))]}
    tree = _apply(design, split)
    split_loads = [_rail(tree, n).budget for n in ("+3V3", "+3V3A")]
    assert [b.load_ma if b else None for b in split_loads] == [20, 0.2]
    whole = {"U1": [_limit("Supply current", 25)]}
    tree = _apply(design, whole)
    for net in ("+3V3", "+3V3A"):
        b = _rail(tree, net).budget
        assert b is not None
        assert b.load_ma == 25
        assert b.loads[0].note == "datasheet p.1, whole part, counted on each of its rails"
    vin = _rail(tree, "VIN").budget
    assert vin is not None and vin.load_ma == 50


def test_a_charger_draws_its_charge_current() -> None:
    parts = {
        "J1": Part("J1", "USB_C", "Connector:USB_C_Receptacle", ""),
        "BT1": Part("BT1", "LiPo", "Device:Battery_Cell", ""),
        "U1": Part("U1", "MCP73831T-2ACI/OT", "Battery_Management:MCP73831", ""),
    }
    nets = {
        "VBUS": [Node("J1", "A4", "VBUS"), Node("U1", "4", "VDD")],
        "+BATT": [Node("U1", "3", "VBAT"), Node("BT1", "1", "+")],
        "/PROG": [Node("U1", "5", "PROG")],
        "GND": [Node("J1", "A1", "GND"), Node("BT1", "2", "-"), Node("U1", "2", "VSS")],
    }
    design = Design(parts, nets)
    charge = Calculation(
        id="cf1",
        kind="charge_current",
        ic="U1",
        pins=["U1.5"],
        value=500.0,
        unit="mA",
        expected=None,
        flagged=False,
        text="",
    )
    tree = _apply(design, {}, [charge])
    b = _rail(tree, "VBUS").budget
    assert b is not None
    assert (b.load_ma, b.unknown) == (500, [])
    assert b.loads[0].note == "charge current (cf1)"
    unknown = _apply(design, {}).rails
    vbus = next(r for r in unknown if r.net == "VBUS").budget
    assert vbus is not None and vbus.unknown == ["U1"]


def test_no_budget_until_applied() -> None:
    tree = power.build(_board(), (), _limits())
    assert all(r.budget is None for r in tree.rails)
    assert "load" not in power.format_power(tree)
