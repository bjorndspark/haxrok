"""Vendor-model circuit simulations: cutting a regulator's circuit out of
the netlist, the decks, the verdicts against the datasheet limits, and
running them (plan 004 step 6)."""

import dataclasses
import itertools
import re
import shutil

import pytest

from app.features.debug import pins, roles
from app.features.debug import spice_circuits as sc
from app.features.debug.design import Design, Node, Part
from app.features.debug.facts import DatasheetReading
from app.features.debug.schemas import (
    LimitFact,
    PowerPart,
    PowerRail,
    PowerTree,
    RailBudget,
    Simulation,
    SpiceModelStatus,
    WiringFix,
)
from app.features.debug.spice import Ngspice, Run
from app.features.debug.spice_convert import ModelFiles

needs_ngspice = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

TPS_PINS = ["AGND", "EN", "FB", "GND", "L1", "L2", "MODE", "PG", "VIN", "VOUT"]
TPS_LIB = b".SUBCKT TPS63802 AGND EN FB GND L1 L2 MODE PG VIN VOUT\nR1 VIN VOUT 1\n.ENDS\n"


def _fact(parameter: str, mn=None, typ=None, mx=None, unit="V", conditions="") -> LimitFact:
    return LimitFact(
        parameter=parameter,
        kind="characteristic",
        pins=[],
        all_pins=False,
        min=mn,
        typ=typ,
        max=mx,
        unit=unit,
        conditions=conditions,
        page=1,
    )


def _design(parts: dict[str, str], nets: dict[str, list[str]]) -> Design:
    """parts: ref -> value. nets: name -> ["REF.PIN(FUNC)", ...]."""

    def node(token: str) -> Node:
        ref_pin, _, func = token.partition("(")
        ref, _, pin = ref_pin.partition(".")
        return Node(ref, pin, func.rstrip(")"))

    return Design(
        {r: Part(r, v, "", "") for r, v in parts.items()},
        {name: [node(t) for t in tokens] for name, tokens in nets.items()},
    )


def _orbit() -> Design:
    """A buck-boost (TPS63802) from a battery to +3.3V, feeding an MCU
    whose NRST has an RC from the rail."""
    return _design(
        {
            "U15": "TPS63802",
            "U16": "STM32",
            "L1": "0.47u",
            "R6": "100k",
            "R7": "1M",
            "R8": "100k",
            "R9": "10k",
            "C9": "100n",
            "C13": "22u",
            "C14": "10u",
        },
        {
            "+BATT": ["U15.1(VIN)", "U15.2(EN)", "C14.1"],
            "+3.3V": ["U15.9(VOUT)", "R7.1", "R6.1", "R9.1", "C13.1", "U16.1(VDD)"],
            "/FB": ["U15.3(FB)", "R7.2", "R8.1"],
            "/L1": ["U15.5(L1)", "L1.1"],
            "/L2": ["U15.6(L2)", "L1.2"],
            "/PG": ["U15.8(PG)", "R6.2"],
            "/NRST": ["U16.7(NRST)", "R9.2", "C9.1"],
            "GND": [
                "U15.4(GND)",
                "U15.10(AGND)",
                "U15.7(MODE)",
                "R8.2",
                "C9.2",
                "C13.2",
                "C14.2",
                "U16.2(VSS)",
            ],
        },
    )


def _tps_facts(dropout: bool = False) -> list[LimitFact]:
    facts = [
        _fact("Input voltage range", 1.3, None, 5.5),
        _fact("Output current", None, None, 2, "A", "VIN = 3.6 V"),
        _fact("Output voltage accuracy", -1, None, 1, "%", "VIN = 3.6 V"),
        _fact("Start-up time", None, 224, None, "µs"),
        _fact("Soft-start time", None, 321, None, "us"),
        _fact("Power-good delay", None, 50, None, "µs"),
    ]
    if dropout:
        facts.append(_fact("Dropout voltage", None, 200, 300, "mV"))
    return facts


def _reading(dropout: bool = False, **more: list[LimitFact]) -> DatasheetReading:
    return DatasheetReading(
        limits={
            "U15": _tps_facts(dropout),
            "U16": [_fact("Supply voltage VDD", 1.7, None, 3.6)],
            **more,
        }
    )


def _status(ref: str = "U15", subckt: str = "TPS63802", **kw) -> SpiceModelStatus:
    base = {
        "ref": ref,
        "value": "TPS63802",
        "status": "pspice",
        "source": "upload",
        "file": "tps63802.lib",
        "subckt": subckt,
        "loads": True,
    }
    return SpiceModelStatus(**{**base, **kw})


def _files(pins: list[str] = TPS_PINS) -> ModelFiles:
    return ModelFiles("tps63802.lib", "TPS63802", pins, {}, {"tps63802.lib": TPS_LIB})


# How the orbit decks' verdicts end when no power tree is given.
ORBIT_SETUP = (
    " From an ideal 3.600 V source on +BATT (the datasheet's typical input);"
    " load the rated 2000 mA, no budget for +3.3V."
)


def _jobs(
    analyses=("startup",), design: Design | None = None, corner: str | None = "nominal", **kw
) -> list[sc.Job]:
    """The orbit's planned decks at ``corner`` (None: every corner's)."""
    got = sc.plan(design or _orbit(), _status(), _files(), _reading(**kw), analyses)
    assert isinstance(got, list)
    return [j for j in got if corner is None or j.corner == corner]


# --- ratings -----------------------------------------------------------------


def test_ratings_read_the_regulators_datasheet_facts():
    rate = sc.ratings(_tps_facts(dropout=True))
    assert rate.current == 2
    assert rate.accuracy == pytest.approx(0.01) and rate.accuracy_cited
    assert rate.startup == pytest.approx(545e-6)  # both times; the PG delay isn't one
    assert rate.dropout == pytest.approx(0.2)
    assert rate.vin == pytest.approx(3.6)  # no typical input: the conditions' VIN


def test_ratings_fall_back_without_facts():
    rate = sc.ratings([_fact("Output current limit", None, 3, None, "A")])
    assert rate.current is None  # a limit isn't a rating
    assert rate.accuracy == 0.05 and not rate.accuracy_cited
    assert rate.startup is None and rate.dropout is None and rate.vin is None
    assert sc.ratings([_fact("Input voltage", 2.5, 3.3, 5.5)]).vin == pytest.approx(3.3)


def test_ratings_take_a_rated_current_given_only_as_a_minimum():
    # TPS63070: "output current: min 2 A (VIN >= 4.5 V ...)"
    assert sc.ratings([_fact("output current", 2, None, None, "A")]).current == 2


def test_ratings_read_a_switch_current_limit_not_an_output_limit():
    rate = sc.ratings(
        [
            _fact("Output current limit", None, 3, None, "A"),
            _fact("Switch current limit", 3.5, 4.0, 4.5, "A"),
        ]
    )
    assert (rate.switch_limit, rate.switch_page) == (4.5, 1)  # the most it lets through
    assert sc.ratings([_fact("Output current limit", None, 3, None, "A")]).switch_limit is None
    assert sc.ratings([_fact("Peak current limit", None, 1800, None, "mA")]).switch_limit == 1.8


def test_supply_range_is_the_widest_that_holds_the_rail():
    facts = [
        _fact("Supply voltage VDD", 1.7, None, 3.6),
        _fact("Analog supply VDDA", 2.4, None, 3.6),
        _fact("VDD rising threshold", 1.6, None, 1.8),
        _fact("Supply voltage VBAT", 1.0, None, 1.8),
    ]
    assert sc.supply_range(facts, 3.3) == (1.7, 3.6)
    assert sc.supply_range(facts[2:], 3.3) is None


# --- decks -------------------------------------------------------------------


def test_the_startup_deck_ramps_the_input_and_switches_on_the_rated_load():
    (job,) = _jobs()
    deck = job.deck
    assert job.ref == "U15" and job.analysis == "startup"
    assert job.title == "U15 (TPS63802) start-up, vendor model"
    assert job.files == {"tps63802.lib": TPS_LIB}
    lines = deck.splitlines()
    assert lines[0] == job.title
    assert '.include "tps63802.lib"' in lines
    assert "Vsrc src 0 PWL(0 0 5e-05 3.6)" in lines  # the datasheet's typical input
    assert "Vam_in src vin 0" in lines  # the input's ammeter
    # 3.3 V at the rated 2 A, switched on once the output passes U16's
    # 1.7 V minimum supply (with 2 % of 3.3 V hysteresis).
    assert "Rload out lsw 1.65" in lines and "Sload lsw 0 out 0 hx_load" in lines
    assert ".model hx_load sw vt=1.634 vh=0.066 ron=0.01 roff=1e9" in lines
    assert job.limits.setup == ORBIT_SETUP.strip()
    # L1 and L2 are switch nodes: each goes through an ammeter from its net.
    assert "XU15 0 vin n1 0 sw_l1 sw_l2 0 n4 vin out TPS63802" in lines
    assert "Vam_l1 n2 sw_l1 0" in lines and "Vam_l2 n3 sw_l2 0" in lines
    assert "* vam_l2: into U15.6 (L2) on /L2" in lines
    for part in ("R7 out n1 1e+06", "R8 n1 0 100000", "L1 n2 n3 4.7e-07", "C13 out 0 2.2e-05"):
        assert part in lines
    assert "R6 out n4 100000" in lines  # the PG pull-up
    assert "C14 vin 0 1e-05" in lines
    # The MCU is the load resistor; its reset RC is kept and probed.
    assert "R9 out rst 10000" in lines and "C9 rst 0 1e-07" in lines
    assert not any(ln.startswith("XU16") for ln in lines)
    assert "Rleak_out out 0 1G" in lines and "Rleak_rst rst 0 1G" in lines
    # A switching model gets the study's solver settings and skips the
    # operating point; PG (n4) is probed, each ammeter's peak measured.
    assert sc._SWITCHING_OPTIONS in lines and sc._LINEAR_OPTIONS not in lines
    assert "save v(out) v(vin) v(rst) v(n4) i(vam_in) i(vam_l1) i(vam_l2)" in lines
    assert "tran 2.5e-07 0.0006495 uic" in lines  # the ramp, then 1.1 × (224 + 321) µs
    for a in ("in", "l1", "l2"):
        assert f"meas tran pk_{a} MAX i(vam_{a})" in lines
        assert f"meas tran nk_{a} MIN i(vam_{a})" in lines
    assert "wrdata out.txt v(out) v(vin) v(rst) v(n4)" in lines
    assert job.limits.reset == "U16.7 (NRST)" and job.limits.pg == "U15.8 (PG)"
    assert job.limits.probes == ("out", "vin", "rst", "n4")
    assert [s.name for s in job.limits.switches] == ["l1", "l2"]
    assert job.limits.switch_limit == 4  # no switch current limit: 2 × the rated 2 A
    assert job.limits.switch_label == "2× the rated 2 A, not cited"


def test_a_cited_switch_current_limit_sets_the_burst_limit():
    more = {"U15": [*_tps_facts(), _fact("Switch current limit", None, 4.0, 4.5, "A")]}
    (job,) = _jobs(**more)
    assert job.limits.switch_limit == 4.5
    assert job.limits.switch_label == "the switch current limit, p. 1"
    assert job.limits.lo == pytest.approx(3.267) and job.limits.hi == pytest.approx(3.333)
    assert (job.limits.over, job.limits.over_label) == (3.6, "U16 supply max")
    assert (job.limits.droop, job.limits.droop_label) == (1.7, "U16 supply min")


def test_load_step_and_dropout_decks():
    jobs = _jobs(("startup", "load_step", "dropout"))
    assert [j.analysis for j in jobs] == ["startup", "load_step"]  # no dropout fact

    step = jobs[1]
    assert "Rload out 0 16.5" in step.deck  # 10 % of the rated current
    assert "Istep out 0 PWL(0 0 0.0006495 0 0.0006505 1.8)" in step.deck
    assert step.limits.t_event == pytest.approx(649.5e-6)
    assert step.limits.stop == pytest.approx(799.5e-6)
    assert step.title == "U15 (TPS63802) load step to 2000 mA, vendor model"

    (drop,) = _jobs(("dropout",), dropout=True)
    vend = 3.3 * 0.99 * 0.9
    assert f"Vsrc src 0 PWL(0 0 5e-05 3.6 0.0006495 3.6 0.0011495 {vend:g})" in drop.deck
    assert drop.limits.vin_end == pytest.approx(vend) and drop.limits.dropout == 0.2


def test_downstream_supply_defaults_to_nominal_ten_percent():
    reading = _reading()
    reading = dataclasses.replace(reading, limits={"U15": _tps_facts()})
    got = sc.plan(_orbit(), _status(), _files(), reading, ["startup"])
    assert isinstance(got, list)
    lim = got[0].limits
    assert lim.over == pytest.approx(3.63) and lim.over_label == "nominal +10 %"
    assert lim.droop == pytest.approx(2.97) and lim.droop_label == "nominal −10 %"


def test_regulators_that_cant_be_set_up_are_skipped_with_the_reason():
    def skip(design=None, status=None, files=None, reading=None) -> str:
        got = sc.plan(
            design or _orbit(),
            status or _status(),
            files or _files(),
            reading or _reading(),
            ["startup"],
        )
        assert isinstance(got, sc.Skip)
        return got.reason

    no_current = _reading()
    no_current = dataclasses.replace(
        no_current, limits={"U15": [f for f in _tps_facts() if "current" not in f.parameter]}
    )
    assert skip(reading=no_current) == (
        "the datasheet facts give no rated output current and its rail has no budget"
    )
    assert skip(files=_files([*TPS_PINS, "SYNC"])) == (
        "its model's pin SYNC has no pin of that name on U15"
    )
    assert skip(status=_status(reason="Its parameters are set by the schematic.")) == (
        "its model needs parameters the schematic sets"
    )

    renamed = _orbit()
    renamed.nets["/VREG"] = renamed.nets.pop("+3.3V")
    assert skip(design=Design(renamed.parts, renamed.nets)) == (
        "its output net /VREG doesn't name a voltage"
    )

    gated = _orbit()
    gated.nets["+BATT"] = [n for n in gated.nets["+BATT"] if n.pin != "2"]
    gated.nets["/EN"] = [Node("U15", "2", "EN"), Node("U16", "9", "PA0")]
    assert skip(design=Design(gated.parts, gated.nets)) == "its EN is driven by U16"


# --- the chain from its source ----------------------------------------------


def _muxed() -> Design:
    """The orbit board behind a fused battery and USB through a power mux
    (U3); U15's EN sits on the mux's battery input."""
    d = _orbit()
    nets = {
        **d.nets,
        "VBAT": [Node("J2", "1", ""), Node("F1", "1", "")],
        "/IN2": [
            Node("F1", "2", ""),
            Node("U3", "2", "IN2"),
            Node("C7", "1", ""),
            Node("U15", "2", "EN"),
        ],
        "VBUS": [Node("J1", "1", "VBUS"), Node("U3", "1", "IN1")],
        "+BATT": [Node("U3", "3", "OUT"), Node("U15", "1", "VIN"), Node("C14", "1", "")],
        "GND": [*d.nets["GND"], Node("C7", "2", ""), Node("U3", "4", "GND")],
    }
    parts = {
        **d.parts,
        "U3": Part("U3", "TPS2121", "", ""),
        "F1": Part("F1", "1A", "Device:Fuse", ""),
        "C7": Part("C7", "10u", "", ""),
        "J1": Part("J1", "USB_C", "", ""),
        "J2": Part("J2", "BATT", "", ""),
    }
    return Design(parts, nets)


def _rail(net: str, lo: float, hi: float, source=None, fed_by=(), budget=None) -> PowerRail:
    return PowerRail(
        net=net,
        source=source,
        min=lo,
        max=hi,
        basis="named",
        inferred=False,
        note="",
        fed_by=list(fed_by),
        loads=[],
        budget=budget,
    )


def _budget(ma: float, unknown=()) -> RailBudget:
    return RailBudget(
        load_ma=ma, unknown=list(unknown), loads=[], rated_ma=2000, rated_note="", over=False
    )


def _muxed_tree(battery=(3.0, 4.2), budget: RailBudget | None = None, **kw) -> PowerTree:
    return PowerTree(
        rails=[
            _rail("VBAT", *battery, source="battery"),
            _rail("VBUS", 5.0, 5.0, source="usb"),
            _rail("/IN2", *battery, fed_by=["F1"]),
            _rail("+BATT", battery[0], 5.0, fed_by=["U3"]),
            _rail("+3.3V", 3.3, 3.3, fed_by=["U15"], budget=budget),
        ],
        parts=[
            PowerPart(ref="F1", value="1A", kind="pass", inputs=["VBAT"], output="/IN2"),
            PowerPart(
                ref="U3", value="TPS2121", kind="switch", inputs=["VBUS", "/IN2"], output="+BATT"
            ),
            PowerPart(
                ref="U15", value="TPS63802", kind="regulator", inputs=["+BATT"], output="+3.3V"
            ),
        ],
        **kw,
    )


def _chain_jobs(
    tree: PowerTree,
    analyses=("startup",),
    design: Design | None = None,
    corner: str | None = "nominal",
) -> list[sc.Job]:
    """The chain's planned decks at ``corner`` (None: every corner's)."""
    got = sc.plan(design or _muxed(), _status(), _files(), _reading(), analyses, tree)
    assert isinstance(got, list), got
    return [j for j in got if corner is None or j.corner == corner]


def test_the_chain_runs_from_the_battery_through_the_fuse_and_mux():
    assert sc.chain(_muxed_tree(), "+BATT") == sc.Chain(
        ("VBAT", "/IN2", "+BATT"),
        tuple(_muxed_tree().parts[:2]),
        "battery",
        3.6,
        3.0,
        4.2,
    )
    (job,) = _chain_jobs(_muxed_tree(budget=_budget(150, ["U16"])))
    lines = job.deck.splitlines()
    assert job.title == "U15 (TPS63802) start-up from VBAT, vendor model"
    # The battery at the middle of its range, its ammeter at the source.
    assert "Vsrc src 0 PWL(0 0 5e-05 3.6)" in lines and "Vam_in src c0 0" in lines
    assert "V_sw_F1 c0 c1 0" in lines and "V_sw_U3 c1 vin 0" in lines
    assert "C7 0 c1 1e-05" in lines  # the mux input's capacitor stays
    assert not any(ln.startswith(("F1 ", "XU3")) for ln in lines)
    # EN on the chain's /IN2 is powered, not driven by U3.
    assert "XU15 0 c1 n1 0 sw_l1 sw_l2 0 n4 vin out TPS63802" in lines
    # 150 mA at 3.3 V from the budget.
    assert "Rload out lsw 22" in lines
    assert job.limits.setup == (
        "From the battery VBAT at 3.600 V (the middle of 3.000 V–4.200 V), as an ideal "
        "source through F1 (1A) as an ideal wire, U3 (TPS2121) as an ideal switch; "
        "load +3.3V's budget, 150 mA, without U16."
    )
    _, text = sc.verdict("startup", job.limits, _rows(649.5e-6, _ramp))
    assert text.endswith(" " + job.limits.setup)


def test_the_chain_takes_the_review_forms_battery_and_falls_back_to_the_rated_load():
    (job,) = _chain_jobs(_muxed_tree(battery=(4.8, 4.8), battery_override=True))
    assert "Vsrc src 0 PWL(0 0 5e-05 4.8)" in job.deck
    assert "Rload out lsw 1.65" in job.deck
    assert job.limits.setup.startswith("From the battery VBAT at 4.800 V (the review form's),")
    assert job.limits.setup.endswith("; load the rated 2000 mA, no budget for +3.3V.")


def test_without_a_battery_the_chain_runs_from_usb():
    tree = _muxed_tree()
    tree = tree.model_copy(update={"rails": [r for r in tree.rails if r.net != "VBAT"]})
    c = sc.chain(tree, "+BATT")
    assert c is not None and (c.nets, c.kind, c.vin) == (("VBUS", "+BATT"), "usb", 5.0)
    # EN on /IN2 is off this chain now: U3 drives it, as far as the cut knows.
    got = sc.plan(_muxed(), _status(), _files(), _reading(), ["startup"], tree)
    assert isinstance(got, sc.Skip) and got.reason == "its EN is driven by U3"

    d = _muxed()
    d.nets["/IN2"].pop()
    d.nets["+BATT"].append(Node("U15", "2", "EN"))
    (job,) = _chain_jobs(tree, design=Design(d.parts, d.nets))
    assert "V_sw_U3 c0 vin 0" in job.deck.splitlines()
    assert job.limits.setup.startswith(
        "From USB VBUS at 5.000 V (USB's nominal, assumed; no min or max run), "
        "as an ideal source through U3"
    )


def test_the_chain_stops_at_an_upstream_regulators_rail():
    tree = PowerTree(
        rails=[
            _rail("VBUS", 5.0, 5.0, source="usb"),
            _rail("+5V", 5.0, 5.0, fed_by=["U2"]),
            _rail("+BATT", 5.0, 5.0, fed_by=["FB1"]),
            _rail("+3.3V", 3.3, 3.3, fed_by=["U15"], budget=_budget(330)),
        ],
        parts=[
            PowerPart(ref="U2", value="BOOST", kind="regulator", inputs=["VBUS"], output="+5V"),
            PowerPart(ref="FB1", value="600R", kind="pass", inputs=["+5V"], output="+BATT"),
            PowerPart(
                ref="U15", value="TPS63802", kind="regulator", inputs=["+BATT"], output="+3.3V"
            ),
        ],
    )
    c = sc.chain(tree, "+BATT")
    assert c is not None and (c.nets, c.kind, c.vin) == (("+5V", "+BATT"), "rail", 5.0)
    got = sc.plan(_orbit(), _status(), _files(), _reading(), ["startup"], tree)
    assert isinstance(got, list)
    assert "Rload out lsw 10" in got[0].deck
    assert got[0].limits.setup.startswith(
        "From U2's output +5V at 5.000 V (one value; no min or max run), as an ideal source"
    )


def test_a_model_without_input_and_output_pins_isnt_a_regulator():
    files = _files(["A", "B", "GND"])
    assert sc.plan(_orbit(), _status(), files, _reading(), ["startup"]) is None


def test_numbered_model_pins_meet_the_boards_repeated_pin_names():
    # TPS63070's model has VIN_0, VIN_1, VOUT_0, VOUT_1; its symbol says VIN, VOUT.
    pins = [*TPS_PINS[:-2], "VIN_0", "VIN_1", "VOUT_0", "VOUT_1"]
    got = sc.plan(_orbit(), _status(), _files(pins), _reading(), ["startup"])
    assert isinstance(got, list)
    lines = got[0].deck.splitlines()
    assert "XU15 0 vin n1 0 sw_l1 sw_l2 0 n4 vin vin out out TPS63802" in lines


def test_a_driver_with_inputs_on_several_nets_isnt_a_regulator():
    # DRV8837's IN1/IN2 and OUT1/OUT2 match the regulator pin names.
    design = _design(
        {"U7": "DRV8837", "U1": "ESP32"},
        {
            "+3V3": ["U7.1(VM)", "U7.8(VCC)", "U1.2(3V3)"],
            "/IN1": ["U7.6(IN1)", "U1.10(IO1)"],
            "/IN2": ["U7.5(IN2)", "U1.11(IO2)"],
            "/Motor/J_MOTOR_OUT_P": ["U7.2(OUT1)"],
            "/Motor/J_MOTOR_OUT_N": ["U7.3(OUT2)"],
            "GND": ["U7.4(GND)", "U1.1(GND)"],
        },
    )
    files = _files(["IN1", "IN2", "VCC", "VM", "GND", "OUT1", "OUT2"])
    status = _status("U7", "DRV8837", value="DRV8837")
    assert sc.plan(design, status, files, _reading(), ["startup"]) is None


# --- verdicts ----------------------------------------------------------------


def _rows(
    stop: float, out, vin=lambda t: 3.6, rst=lambda t: 0.0, pg=lambda t: 0.0, step: float = 5e-6
) -> list[tuple[float, ...]]:
    """(t, out, vin, rst, pg) samples: the orbit decks' probes."""
    n = round(stop / step)
    return [(t, out(t), vin(t), rst(t), pg(t)) for t in (i * step for i in range(n + 1))]


def _ramp(t: float, rise: float = 200e-6, top: float = 3.3) -> float:
    return min(t / rise, 1.0) * top


def test_a_clean_startup_passes():
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6))
    got, text = sc.verdict("startup", job.limits, rows)
    assert got == "passed"
    assert text.startswith("Reaches 3.267 V at ")
    assert "settles at 3.300 V" in text and "ceiling 3.600 V (U16 supply max)" in text
    assert text.endswith(" U15.8 (PG) stays low." + ORBIT_SETUP)

    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6), pg=lambda t: _ramp(t, 400e-6))
    _, text = sc.verdict("startup", job.limits, rows)
    assert "U15.8 (PG) rises at 200 µs." in text  # half its 3.3 V peak


def test_a_burst_into_a_switch_pin_above_the_limit_is_flagged():
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6))
    peaks = {"in": (1.2, 60e-6), "l1": (0.9, 300e-6), "l2": (12.5, 80e-6)}
    got, text = sc.verdict("startup", job.limits, rows, peaks)
    assert got == "flagged"
    assert text.startswith(
        "U15 draws 12.50 A into U15.6 (L2) on /L2 at 80 µs, above 4.00 A "
        "(2× the rated 2 A, not cited). Reaches 3.267 V at "
    )
    assert text.endswith(
        " Peak currents: into U15.5 (L1) on /L1 0.90 A at 300 µs; "
        "into U15.6 (L2) on /L2 12.50 A at 80 µs; from the input 1.20 A at 60 µs." + ORBIT_SETUP
    )

    got, text = sc.verdict("startup", job.limits, rows, {**peaks, "l2": (1.8, 80e-6)})
    assert got == "passed" and "into U15.6 (L2) on /L2 1.80 A at 80 µs" in text

    # A wrong output and a burst are both said.
    low = _rows(649.5e-6, lambda t: _ramp(t, top=3.1))
    got, text = sc.verdict("startup", job.limits, low, peaks)
    assert got == "flagged" and text.startswith("U15's output never reaches 3.267 V")
    assert ". U15 draws 12.50 A into U15.6 (L2)" in text


def test_startup_flags_overshoot_a_missed_band_and_an_early_reset():
    (job,) = _jobs()

    def overshoot(t: float) -> float:
        return 3.8 if 200e-6 <= t <= 220e-6 else _ramp(t)

    got, text = sc.verdict("startup", job.limits, _rows(649.5e-6, overshoot, rst=lambda t: 0.0))
    assert got == "flagged"
    assert "peaks at 3.800 V, above 3.600 V (U16 supply max)" in text

    got, text = sc.verdict("startup", job.limits, _rows(649.5e-6, lambda t: _ramp(t, top=3.1)))
    assert got == "flagged" and "never reaches 3.267 V" in text
    assert "settles at 3.100 V, outside 3.267 V–3.333 V (±1 %)" in text

    early = _rows(649.5e-6, lambda t: _ramp(t, 400e-6), rst=lambda t: _ramp(t, 100e-6))
    got, text = sc.verdict("startup", job.limits, early)
    assert got == "flagged"
    assert "releases U16.7 (NRST) at " in text and "before the rail reaches 3.267 V" in text


def test_load_step_flags_a_droop_below_the_downstream_minimum():
    _, step = _jobs(("startup", "load_step"))
    t1 = step.limits.t_event

    def dip(depth: float):
        return lambda t: depth if t1 <= t <= t1 + 10e-6 else _ramp(t)

    got, text = sc.verdict("load_step", step.limits, _rows(step.limits.stop, dip(3.1)))
    assert got == "passed"
    assert text.startswith("Droops to 3.100 V at the step, back in band ")

    got, text = sc.verdict("load_step", step.limits, _rows(step.limits.stop, dip(1.5)))
    assert got == "flagged"
    assert "droops to 1.500 V, below 1.700 V (U16 supply min)" in text


def test_dropout_flags_regulation_lost_close_to_the_nominal_input():
    (drop,) = _jobs(("dropout",), dropout=True)
    lim = drop.limits
    t1, stop = lim.t_event, lim.stop

    def vin(t: float) -> float:
        return 3.6 if t <= t1 else 3.6 - (3.6 - lim.vin_end) * (t - t1) / (stop - t1)

    def follows(margin: float):
        return lambda t: min(_ramp(t), vin(t) - margin)

    got, text = sc.verdict("dropout", lim, _rows(stop, _ramp, vin))  # a buck-boost holds on
    assert got == "passed"
    assert text == (
        "Stays in band (3.267 V–3.333 V (±1 %)) down to 2.940 V in. "
        "The datasheet's dropout puts it near 3.500 V." + ORBIT_SETUP
    )

    got, text = sc.verdict("dropout", lim, _rows(stop, follows(0.2), vin))
    assert got == "flagged"
    assert "leaves its band at 3.461 V in, only 0.139 V below the nominal 3.600 V" in text

    got, text = sc.verdict(
        "dropout", dataclasses.replace(lim, vin=4.0), _rows(stop, follows(0.2), vin)
    )
    assert got == "passed" and text.startswith(
        "Leaves its band (3.267 V–3.333 V (±1 %)) at 3.461 V"
    )


# --- results -----------------------------------------------------------------


def _wrdata(rows: list[tuple[float, ...]]) -> str:
    return "\n".join(" ".join(f"{r[0]:.6e} {v:.6e}" for v in r[1:]) for r in rows) + "\n"


def test_parse_columns_takes_one_time_column():
    text = " 0.0 1.0 0.0 3.6\n 1e-6 1.5 1e-6 3.6\n"
    assert sc.parse_columns(text, 2) == [(0.0, 1.0, 3.6), (1e-6, 1.5, 3.6)]
    with pytest.raises(ValueError, match="no samples"):
        sc.parse_columns("No. of Data Rows : 0\n", 2)


def test_parse_peaks_reads_the_meas_lines():
    log = (
        "pk_in               =  2.880005e+00 at=  5.000000e-05\n"
        "pk_l2               =  1.772828e+00 at=  3.747500e-04\n"
        "pk_l1 = nan at= nan\n"
        "nk_in               = -3.000000e-01 at=  1.000000e-05\n"
        "nk_l2               = -2.500000e+00 at=  2.000000e-04\n"
        "Error: measure  pk_x  (MAX) : out of interval\n"
    )
    peaks = sc.parse_peaks(log)
    assert peaks["in"] == (2.880005, 5e-5) and peaks["l2"] == (-2.5, 2e-4)
    assert "x" not in peaks


def test_result_says_passed_timed_out_or_failed():
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6), step=0.25e-6)
    sim = sc.result("vm1", job, Run(0, _wrdata(rows), ""))
    assert (sim.id, sim.ref, sim.analysis, sim.verdict) == ("vm1", "U15", "startup", "passed")
    assert sim.calc_id is None and sim.t_unit == "µs"
    assert len(sim.t) <= 240 and sim.t[-1] == pytest.approx(649.5)
    assert max(sim.v) == pytest.approx(3.3)
    assert [lv.label for lv in sim.levels] == [
        "band low (±1 %)",
        "band high (±1 %)",
        "U16 supply max",
    ]

    sim = sc.result("vm1", job, Run(None, None, "partial"))
    assert sim.verdict == "timed_out" and sim.text.startswith("ngspice was stopped after ")
    assert sim.t == [] and sim.v == []

    sim = sc.result("vm1", job, Run(1, None, "Error: unknown subckt: TPS63802\n"))
    assert sim.verdict == "failed" and "unknown subckt" in sim.text

    sim = sc.result("vm1", job, Run(0, "garbage\n", ""))
    assert sim.verdict == "failed"
    assert sim.text == "The run failed: its output was unreadable (no samples)"


def test_result_reads_the_ammeters_peaks_from_the_log():
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6), step=0.25e-6)
    sim = sc.result("vm1", job, Run(0, _wrdata(rows), "pk_l2 = 1.25e+01 at= 8.0e-05\n"))
    assert sim.verdict == "flagged" and sim.text.startswith("U15 draws 12.50 A into U15.6 (L2)")
    assert [(p.name, p.label, p.a, p.t_s) for p in sim.peaks] == [
        ("l2", "into U15.6 (L2) on /L2", 12.5, 8e-5)
    ]
    assert sim.fix_of is None and sim.finding is None


def test_a_burst_out_of_a_switch_pin_counts_too():
    # L1 sources its inductor's current: the burst is the MIN, negative.
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6), step=0.25e-6)
    log = "pk_l2 = 1.0e-03 at= 3.0e-04\nnk_l2 = -1.25e+01 at= 8.0e-05\n"
    sim = sc.result("vm1", job, Run(0, _wrdata(rows), log))
    assert sim.verdict == "flagged"
    assert sim.text.startswith("U15 draws 12.50 A out of U15.6 (L2)")
    assert [(p.label, p.a) for p in sim.peaks] == [("out of U15.6 (L2) on /L2", -12.5)]


def test_a_skip_becomes_a_simulation_without_samples():
    sim = sc.skipped("vm3", sc.Skip("U17", "U17 (LDO)", "it has no rated current"))
    assert (sim.id, sim.verdict, sim.deck, sim.t, sim.levels) == ("vm3", "skipped", "", [], [])
    assert sim.title == "U17 (LDO), vendor model"
    assert sim.text == "Not simulated: it has no rated current."


# --- running -----------------------------------------------------------------


class FakeRunner:
    """Answers a start-up deck with a clean ramp, and a load-step deck with
    None (as if ngspice weren't installed)."""

    def __init__(self) -> None:
        self.decks: list[str] = []

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        self.decks.append(netlist)
        if "Istep" in netlist:
            return None
        rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6))
        return Run(0, _wrdata(rows), "")


async def test_simulate_yields_skips_first_then_each_verdict():
    design = _orbit()
    design.nets["/VREG"] = [Node("U17", "3", "VOUT")]
    design.nets["+BATT"].append(Node("U17", "1", "VIN"))
    design.nets["GND"].append(Node("U17", "2", "GND"))
    design = Design(design.parts, design.nets)
    upload = {
        "tps63802.lib": TPS_LIB,
        "ldo.lib": b".SUBCKT LDO1 VIN GND VOUT\nR1 VIN VOUT 1\n.ENDS\n",
    }
    statuses = [
        _status(),
        _status("U17", "LDO1", file="ldo.lib"),
        _status("U18", loads=False),  # never loaded: not simulated at all
    ]
    runner = FakeRunner()
    sims = [
        s
        async for s in sc.simulate(
            statuses, upload, design, _reading(), ["startup", "load_step"], runner
        )
    ]
    # vm1 start-up and vm2 load step at nominal, vm3 start-up from the bench.
    assert [(s.id, s.ref, s.verdict) for s in sims] == [
        ("vm4", "U17", "skipped"),
        ("vm1", "U15", "passed"),
        ("vm3", "U15", "passed"),
    ]
    assert "/VREG doesn't name a voltage" in sims[0].text
    assert len(runner.decks) == 3  # the load step ran but ngspice "wasn't there"

    assert [s async for s in sc.simulate(statuses, upload, design, _reading(), [], runner)] == []


def _l2_shorted() -> Design:
    """The orbit board with L2 drawn onto its own output, as the
    orbit-l2-shorted eval case and the private board have it."""
    design = _orbit()
    nets = {k: v for k, v in design.nets.items() if k != "/L2"}
    nets["+3.3V"] = [*nets["+3.3V"], Node("U15", "6", "L2"), Node("L1", "2", "")]
    return Design(design.parts, nets)


async def test_a_fixed_finding_runs_its_chain_again_with_the_fix():
    design = _l2_shorted()
    [finding] = pins.lookup(design).findings
    assert finding.fix is not None and finding.fix.pins == ["U15.6", "L1.2"]
    fixed = sc.Fixed(finding.id, finding.fix, roles.apply_fix(design, finding.fix), None)
    runner = FakeRunner()
    sims = [
        s
        async for s in sc.simulate(
            [_status()],
            {"tps63802.lib": TPS_LIB},
            design,
            _reading(),
            ["startup"],
            runner,
            None,
            [fixed],
        )
    ]
    # Nominal (vm1) and bench (vm2) as built, each again with the fix.
    built, bench, with_fix, bench_fix = sorted(sims, key=lambda s: s.id)
    assert (built.id, built.fix_of, built.finding) == ("vm1", None, None)
    assert (with_fix.id, with_fix.fix_of, with_fix.finding) == ("vm3", "vm1", "wf1")
    assert (bench_fix.id, bench_fix.fix_of) == ("vm4", "vm2")
    assert with_fix.title == f"{built.title}, with wf1's fix"
    assert bench_fix.title == f"{bench.title}, with wf1's fix"
    # Queued nominal before bench, each as built before its fix. As built
    # the L2 ammeter sits on the output; with the fix, on its own net.
    assert ["Vsrc sup" in d for d in runner.decks] == [False, False, True, True]
    assert ["Vam_l2 out sw_l2 0" in d for d in runner.decks] == [True, False, True, False]


async def test_a_fix_elsewhere_doesnt_run_again():
    design = _orbit()
    fix = WiringFix(
        pins=["U9.3", "L4.2"], leaves="/SW9", joins="Net-(U9-SW)", new_net=True, text=""
    )
    fixed = sc.Fixed("wf1", fix, design, None)
    sims = [
        s
        async for s in sc.simulate(
            [_status()],
            {"tps63802.lib": TPS_LIB},
            design,
            _reading(),
            ["startup"],
            FakeRunner(),
            None,
            [fixed],
        )
    ]
    assert [s.id for s in sims] == ["vm1", "vm2"]  # nominal and bench, no fix


# --- corners and the time budget (plan 006 step 4) ---------------------------


def test_startup_runs_at_nominal_bench_min_and_max():
    jobs = _chain_jobs(_muxed_tree(), ["startup", "load_step"], corner=None)
    assert [(j.analysis, j.corner) for j in jobs] == [
        ("startup", "nominal"),
        ("load_step", "nominal"),
        ("startup", "bench"),
        ("startup", "min"),
        ("startup", "max"),
    ]
    _, _, bench, lo, hi = jobs
    assert bench.title == (
        "U15 (TPS63802) start-up from VBAT on a 1 A current-limited supply (assumed), vendor model"
    )
    assert (
        lo.title == "U15 (TPS63802) start-up from VBAT at min 3.000 V, C and L −20 %, vendor model"
    )
    assert (
        hi.title == "U15 (TPS63802) start-up from VBAT at max 4.200 V, C and L −20 %, vendor model"
    )
    assert "Vsrc src 0 PWL(0 0 5e-05 3)" in lo.deck and "Vsrc src 0 PWL(0 0 5e-05 4.2)" in hi.deck
    assert (lo.limits.vin, hi.limits.vin) == (3.0, 4.2)
    assert lo.limits.setup == (
        "From the battery VBAT at 3.000 V (its min), as an ideal source through F1 (1A) as "
        "an ideal wire, U3 (TPS2121) as an ideal switch; load the rated 2000 mA, no budget "
        "for +3.3V; ceramic C and L −20 %."
    )


def test_the_bench_supply_is_an_ideal_source_behind_a_smooth_current_limit():
    (bench,) = _chain_jobs(_muxed_tree(), corner="bench")
    lines = bench.deck.splitlines()
    assert "Vsrc sup 0 PWL(0 0 5e-05 3.6)" in lines
    assert "Bbench sup src I=1*tanh((v(sup)-v(src))/0.05)" in lines
    assert "Vam_in src c0 0" in lines  # the ammeter still reads what it gives
    assert bench.limits.setup.startswith(
        "From the battery VBAT at 3.600 V (the middle of 3.000 V–4.200 V), "
        "as a 1 A current-limited supply (assumed) through F1"
    )
    # Without a chain, the regulator's own input is the bench supply's.
    (alone,) = _jobs(corner="bench")
    assert alone.limits.setup.startswith(
        "From a 1 A current-limited 3.600 V supply (assumed) on +BATT"
    )


def test_the_bench_limit_is_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sc.settings, "BENCH_CURRENT_LIMIT_A", 0.5)
    (bench,) = _jobs(corner="bench")
    assert "Bbench sup src I=0.5*tanh((v(sup)-v(src))/0.025)" in bench.deck
    assert "on a 0.5 A current-limited supply (assumed)" in bench.title


def test_min_and_max_derate_ceramic_c_and_l_but_not_a_polarized_cap():
    d = _muxed()
    parts = {**d.parts, "C13": Part("C13", "22u", "Device:C_Polarized", "")}
    design = Design(parts, d.nets)
    jobs = {
        j.corner: j.deck.splitlines()
        for j in _chain_jobs(_muxed_tree(), design=design, corner=None)
    }
    for corner in ("nominal", "bench"):
        assert "L1 n2 n3 4.7e-07" in jobs[corner] and "C14 vin 0 1e-05" in jobs[corner]
        assert "C9 rst 0 1e-07" in jobs[corner]
    for corner in ("min", "max"):
        assert "L1 n2 n3 3.76e-07" in jobs[corner]
        assert "C14 vin 0 8e-06" in jobs[corner] and "C7 0 c1 8e-06" in jobs[corner]
        assert "C9 rst 0 8e-08" in jobs[corner]  # the reset RC's too
        assert "C13 out 0 2.2e-05" in jobs[corner]  # polarized: no DC-bias derating


def test_a_source_with_one_voltage_has_no_min_or_max():
    tree = _muxed_tree(battery=(4.8, 4.8), battery_override=True)
    assert [j.corner for j in _chain_jobs(tree, corner=None)] == ["nominal", "bench"]
    assert [j.corner for j in _jobs(corner=None)] == ["nominal", "bench"]  # no chain


def test_runs_queue_by_corner_each_as_built_before_its_fix():
    planned = _chain_jobs(_muxed_tree(), corner=None)
    jobs = [(f"vm{k}", j) for k, j in enumerate(planned, 1)]
    fixes = [
        (f"vm{k}", dataclasses.replace(j, fix_of=i)) for k, (i, j) in enumerate(jobs, len(jobs) + 1)
    ]
    queued = sc._queued([*reversed(jobs), *fixes])
    assert [(i, j.corner, j.fix_of) for i, j in queued] == [
        ("vm1", "nominal", None),
        ("vm5", "nominal", "vm1"),
        ("vm2", "bench", None),
        ("vm6", "bench", "vm2"),
        ("vm3", "min", None),
        ("vm7", "min", "vm3"),
        ("vm4", "max", None),
        ("vm8", "max", "vm4"),
    ]


async def test_a_run_still_queued_when_the_budget_is_spent_is_skipped():
    design = _l2_shorted()
    [finding] = pins.lookup(design).findings
    assert finding.fix is not None
    fixed = sc.Fixed(finding.id, finding.fix, roles.apply_fix(design, finding.fix), None)
    ticks = itertools.chain([0.0, 0.0, 300.0], itertools.repeat(600.0))  # start, vm1, vm3, …
    runner = FakeRunner()
    sims = [
        s
        async for s in sc.simulate(
            [_status()],
            {"tps63802.lib": TPS_LIB},
            design,
            _reading(),
            ["startup"],
            runner,
            None,
            [fixed],
            budget_s=600,
            clock=lambda: next(ticks),
        )
    ]
    got = {s.id: s for s in sims}
    assert len(runner.decks) == 2  # nominal as built and with the fix
    assert (got["vm1"].verdict, got["vm3"].verdict) == ("passed", "passed")
    assert (got["vm2"].verdict, got["vm4"].verdict) == ("skipped", "skipped")
    assert got["vm4"].fix_of == "vm2" and got["vm4"].finding == "wf1"  # still paired
    assert got["vm2"].title.endswith("current-limited supply (assumed), vendor model")
    assert got["vm2"].text == (
        "Not simulated: over the time budget (10 min for all of a request's vendor-model runs)."
    )


@needs_ngspice
async def test_a_toy_regulator_starts_up_in_real_ngspice():
    design = _design(
        {"U1": "TOYREG", "C1": "10u"},
        {
            "+5V": ["U1.1(VIN)", "U1.3(EN)"],
            "+3V3": ["U1.4(VOUT)", "C1.1"],
            "GND": ["U1.2(GND)", "C1.2"],
        },
    )
    upload = {
        "toy.lib": b".SUBCKT TOYREG VIN GND EN VOUT\n"
        b"B1 INT GND V=min(V(VIN,GND), 3.3)\n"
        b"RS INT VOUT 0.01\n"
        b".ENDS\n"
    }
    reading = DatasheetReading(
        limits={
            "U1": [
                _fact("Output current", None, None, 0.5, "A"),
                _fact("Start-up time", None, 100, None, "µs"),
            ]
        }
    )
    status = _status("U1", "TOYREG", value="TOYREG", status="ngspice", file="toy.lib")
    sim, bench = sorted(
        [
            s
            async for s in sc.simulate(
                [status], upload, design, reading, ["startup"], Ngspice(timeout_s=60)
            )
        ],
        key=lambda s: s.id,
    )
    assert sim.verdict == "passed", sim.text
    # The bench supply's behavioural current limit runs in ngspice too.
    assert bench.verdict == "passed", bench.text
    assert "Bbench sup src I=1*tanh((v(sup)-v(src))/0.05)" in bench.deck
    assert sim.t_unit == "µs" and sim.t[-1] == pytest.approx(160)
    assert sim.v[-1] == pytest.approx(3.3, abs=0.01)
    # No switch-node pin: the linear solver settings, the operating point solved.
    assert sc._LINEAR_OPTIONS in sim.deck and " uic" not in sim.deck
    assert "meas tran pk_in MAX i(vam_in)" in sim.deck


# --- a mux's model on the chain (plan 008 step 1) ----------------------------

MUX_LIB = b".SUBCKT TPS2121_TRANS IN1 IN2 VOUT_0 VOUT_1 GND\nR1 IN1 VOUT_0 1\n.ENDS\n"
MUX_PINS = ["IN1", "IN2", "VOUT_0", "VOUT_1", "GND"]


def _mux_files(pins: list[str] = MUX_PINS, spiceinit: bytes | None = None) -> ModelFiles:
    files = {"tps2121.lib": MUX_LIB}
    if spiceinit is not None:
        files[".spiceinit"] = spiceinit
    return ModelFiles("tps2121.lib", "TPS2121_TRANS", pins, {}, files)


def _muxed_job(mux: sc.Mux | str) -> sc.Job:
    got = sc.plan(
        _muxed(), _status(), _files(), _reading(), ["startup"], _muxed_tree(), {"U3": mux}
    )
    assert isinstance(got, list), got
    return next(j for j in got if j.corner == "nominal")


def test_a_mux_whose_model_draws_nothing_goes_in_wrapped():
    job = _muxed_job(sc.Mux(_mux_files(), wrap=True, share=0.02))
    lines = job.deck.splitlines()
    # VOUT_0/VOUT_1 land on the board's OUT; the other input (VBUS) is its own node.
    assert "XU3 n1 c1 mx_u3 mx_u3 0 TPS2121_TRANS" in lines
    # The output through a sense source, the same current drawn from the input.
    assert "Vsense_U3 mx_u3 vin 0" in lines and "FU3 c1 0 Vsense_U3 1" in lines
    assert "Rleak_n1 n1 0 1G" in lines  # VBUS unpowered
    assert not any(ln.startswith("V_sw_U3") for ln in lines)
    assert '.include "tps63802.lib"' in lines and '.include "tps2121.lib"' in lines
    assert job.files["tps2121.lib"] == MUX_LIB and job.files["tps63802.lib"] == TPS_LIB
    assert sc._SWITCHING_OPTIONS in lines
    assert job.limits.setup.startswith(
        "From the battery VBAT at 3.600 V (the middle of 3.000 V–4.200 V), as an ideal "
        "source through F1 (1A) as an ideal wire, U3 (TPS2121) as its vendor model "
        "TPS2121_TRANS, its load current drawn from its input (the model draws 2 % of it "
        "there); load"
    )


def test_a_mux_whose_model_draws_its_load_goes_in_as_it_is():
    job = _muxed_job(sc.Mux(_mux_files(), wrap=False, share=0.98))
    lines = job.deck.splitlines()
    assert "XU3 n1 c1 vin vin 0 TPS2121_TRANS" in lines
    assert not any(ln.startswith(("Vsense_", "FU3")) for ln in lines)
    assert "U3 (TPS2121) as its vendor model TPS2121_TRANS; load" in job.limits.setup


def test_a_mux_that_starts_only_from_a_step_steps_the_chains_source():
    got = sc.plan(
        _muxed(),
        _status(),
        _files(),
        _reading(),
        ["startup"],
        _muxed_tree(),
        {"U3": sc.Mux(_mux_files(), wrap=True, share=0.02, stepped=True)},
    )
    assert isinstance(got, list) and {j.corner for j in got} == {"nominal", "bench", "min", "max"}
    for job in (j for j in got if j.corner != "bench"):
        # Held at the corner's voltage from 0 s.
        vsrc = next(ln for ln in job.deck.splitlines() if ln.startswith("Vsrc"))
        assert re.fullmatch(r"Vsrc src 0 PWL\(0 (\S+) 5e-05 \1\)", vsrc), vsrc
        assert (
            "TPS2121_TRANS, its load current drawn from its input (the model draws 2 % of it "
            "there), the source stepped on at 0 s (the model doesn't start from a ramp); load"
        ) in job.limits.setup
    nominal = next(j for j in got if j.corner == "nominal")
    assert "Vsrc src 0 PWL(0 3.6 5e-05 3.6)" in nominal.deck.splitlines()
    # Behind the bench supply's limit: ramped, and the mux an ideal switch.
    bench = next(j for j in got if j.corner == "bench")
    assert "Vsrc sup 0 PWL(0 0 5e-05 3.6)" in bench.deck.splitlines()
    assert "V_sw_U3 c1 vin 0" in bench.deck.splitlines()
    assert (
        "U3 (TPS2121) as an ideal switch (its model starts only from a stepped input, and "
        "the supply's limit ramps it)"
    ) in bench.limits.setup


@pytest.mark.parametrize(
    ("mux", "reason"),
    [
        ("its model's probe run failed: boom", "its model's probe run failed: boom"),
        (
            sc.Mux(_mux_files([*MUX_PINS, "ILIM"]), wrap=True, share=0.0),
            "its model's pin ILIM has no pin of that name on U3",
        ),
        (
            sc.Mux(_mux_files(spiceinit=b"set ngbehavior=lt\n"), wrap=True, share=0.0),
            "its model's dialect differs from the regulator's",
        ),
    ],
)
def test_a_mux_falls_back_to_an_ideal_switch_saying_why(mux, reason):
    job = _muxed_job(mux)
    lines = job.deck.splitlines()
    assert "V_sw_U3 c1 vin 0" in lines and not any(ln.startswith("XU3") for ln in lines)
    assert "tps2121.lib" not in job.files
    assert f"U3 (TPS2121) as an ideal switch ({reason});" in job.limits.setup


def _probe_log(iin: float, iout: float) -> str:
    return (
        f"iin                 =  {iin:e} from=  8.000000e-04 to=  1.000000e-03\n"
        f"iout                =  {iout:e} from=  8.000000e-04 to=  1.000000e-03\n"
    )


def test_the_probe_wraps_a_model_whose_input_draws_too_little():
    m = _mux_files()
    assert sc.probed(m, Run(0, "", _probe_log(2e-3, 0.1))) == sc.Mux(m, True, 0.02)
    assert sc.probed(m, Run(0, "", _probe_log(0.1, 0.1))) == sc.Mux(m, False, 1.0)
    assert sc.probed(m, Run(0, "", _probe_log(0.0, 0.01))) == (
        "its model's output didn't come up in its probe run (10 mA of 100 mA)"
    )
    assert sc.probed(m, Run(None, None, "")) == "its model's probe run timed out"
    assert sc.probed(m, Run(1, None, "Error: unknown subckt: x")).startswith(
        "its model's probe run failed: "
    )


class ProbeRunner(FakeRunner):
    """Answers a mux's probe with an input drawing 1 % of the output."""

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        if "model probe" in netlist.splitlines()[0]:
            self.decks.append(netlist)
            return Run(0, "", _probe_log(1e-3, 0.1))
        return await super().transient(netlist, files)


class StepOnlyRunner(FakeRunner):
    """Answers a mux's probe as TI's TPS2121 model runs: its output stays
    off from a ramped input and comes up from a stepped one (``stepped``
    False: from neither)."""

    def __init__(self, stepped: bool = True) -> None:
        super().__init__()
        self.stepped = stepped

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        title = netlist.splitlines()[0]
        if "model probe" in title:
            self.decks.append(netlist)
            up = self.stepped and " stepped," in title
            return Run(0, "", _probe_log(1e-4, 0.1 if up else 5e-5))
        return await super().transient(netlist, files)


def _mux_status() -> SpiceModelStatus:
    return _status("U3", "TPS2121_TRANS", value="TPS2121", file="tps2121.lib")


async def test_each_mux_is_probed_once_from_its_best_input():
    runner = ProbeRunner()
    upload = {"tps2121.lib": MUX_LIB}
    got = await sc.probe_muxes([_mux_status(), _status()], upload, _muxed(), _muxed_tree(), runner)
    assert set(got) == {"U3"} and isinstance(got["U3"], sc.Mux)
    assert got["U3"].wrap and got["U3"].share == pytest.approx(0.01)
    (deck,) = runner.decks
    lines = deck.splitlines()
    # USB ranks above the fused battery's /IN2, which has no source of its own.
    assert lines[0] == "U3's model probe from VBUS at 5.000 V, 0.1 A"
    assert "Vsrc src 0 PWL(0 0 5e-05 5)" in lines and "Rprobe lo 0 50" in lines
    assert "XU3 vin n1 out out 0 TPS2121_TRANS" in lines
    assert "C7 0 n1 1e-05" in lines and "Rleak_n1 n1 0 1G" in lines
    assert "meas tran iout AVG i(vam_out) from=0.0008 to=0.001" in lines


async def test_simulate_puts_the_probed_mux_in_the_chain():
    runner = ProbeRunner()
    sims = [
        s
        async for s in sc.simulate(
            [_mux_status(), _status()],
            {"tps2121.lib": MUX_LIB, "tps63802.lib": TPS_LIB},
            _muxed(),
            _reading(),
            ["startup"],
            runner,
            _muxed_tree(),
        )
    ]
    assert sims and all(s.ref == "U15" for s in sims)
    chain = [d for d in runner.decks if "model probe" not in d.splitlines()[0]]
    assert chain and all("FU3 c1 0 Vsense_U3 1" in d for d in chain)


@needs_ngspice
async def test_a_toy_mux_that_draws_nothing_is_wrapped_and_the_source_sees_the_load():
    design = _design(
        {"U1": "TOYREG", "U2": "TOYMUX", "C1": "10u", "C2": "1u"},
        {
            "VBUS": ["U2.1(IN1)"],
            "/IN2": ["U2.2(IN2)"],
            "+SYS": ["U2.3(OUT)", "U1.1(VIN)", "U1.3(EN)", "C2.1"],
            "+3V3": ["U1.4(VOUT)", "C1.1"],
            "GND": ["U1.2(GND)", "U2.4(GND)", "C1.2", "C2.2"],
        },
    )
    upload = {
        # A linear regulator: its load current drawn from VIN.
        "toy.lib": b".SUBCKT TOYREG VIN GND EN VOUT\n"
        b"B1 INT GND V=min(V(VIN,GND), 3.3)\n"
        b"VS INT X 0\n"
        b"RS X VOUT 0.01\n"
        b"F1 VIN GND VS 1\n"
        b".ENDS\n",
        # The output follows the higher input without drawing from it.
        "mux.lib": b".SUBCKT TOYMUX IN1 IN2 OUT GND\n"
        b"B1 INT GND V=max(V(IN1,GND), V(IN2,GND))\n"
        b"RS INT OUT 0.01\n"
        b".ENDS\n",
    }
    tree = PowerTree(
        rails=[
            _rail("VBUS", 5.0, 5.0, source="usb"),
            _rail("+SYS", 5.0, 5.0, fed_by=["U2"]),
            _rail("+3V3", 3.3, 3.3, fed_by=["U1"]),
        ],
        parts=[
            PowerPart(
                ref="U2", value="TOYMUX", kind="switch", inputs=["VBUS", "/IN2"], output="+SYS"
            ),
            PowerPart(ref="U1", value="TOYREG", kind="regulator", inputs=["+SYS"], output="+3V3"),
        ],
    )
    reading = DatasheetReading(
        limits={
            "U1": [
                _fact("Output current", None, None, 0.5, "A"),
                _fact("Start-up time", None, 100, None, "µs"),
            ]
        }
    )
    reg = _status("U1", "TOYREG", value="TOYREG", status="ngspice", file="toy.lib")
    mux = _status("U2", "TOYMUX", value="TOYMUX", status="ngspice", file="mux.lib")
    ngspice = Ngspice(timeout_s=60)
    probed = await sc.probe_muxes([mux, reg], upload, design, tree, ngspice)
    assert isinstance(probed["U2"], sc.Mux) and probed["U2"].wrap, probed
    assert abs(probed["U2"].share) < 0.01
    sims = [
        s
        async for s in sc.simulate([mux, reg], upload, design, reading, ["startup"], ngspice, tree)
    ]
    sim = next(s for s in sims if s.id == "vm1")
    assert sim.verdict == "passed", sim.text
    assert "FU2 c0 0 Vsense_U2 1" in sim.deck.splitlines()
    # Through the F-source the source sees C1 charging (1e5 V/s on 10 µF),
    # the 0.5 A load and C2: about 1.6 A. Without it, only C2's 0.1 A.
    (peak,) = [p for p in sim.peaks if p.name == "in"]
    assert peak.a == pytest.approx(1.6, rel=0.1), sim.peaks


async def test_a_mux_down_from_a_ramp_is_probed_again_stepped():
    runner = StepOnlyRunner()
    upload = {"tps2121.lib": MUX_LIB}
    got = await sc.probe_muxes([_mux_status(), _status()], upload, _muxed(), _muxed_tree(), runner)
    mux = got["U3"]
    assert isinstance(mux, sc.Mux) and mux.stepped and mux.wrap
    assert mux.share == pytest.approx(1e-3)
    ramped, stepped = runner.decks
    assert "Vsrc src 0 PWL(0 0 5e-05 5)" in ramped.splitlines()
    assert stepped.splitlines()[0] == "U3's model probe from VBUS at 5.000 V stepped, 0.1 A"
    assert "Vsrc src 0 PWL(0 5 5e-05 5)" in stepped.splitlines()


async def test_a_mux_down_from_a_step_too_keeps_the_ramps_reason():
    runner = StepOnlyRunner(stepped=False)
    upload = {"tps2121.lib": MUX_LIB}
    got = await sc.probe_muxes([_mux_status(), _status()], upload, _muxed(), _muxed_tree(), runner)
    assert got["U3"] == "its model's output didn't come up in its probe run (0 mA of 100 mA)"
    assert len(runner.decks) == 2


async def test_a_failed_probe_is_not_run_again_stepped():
    class Failing(FakeRunner):
        async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
            self.decks.append(netlist)
            return Run(1, None, "Error: unknown subckt: x")

    runner = Failing()
    upload = {"tps2121.lib": MUX_LIB}
    got = await sc.probe_muxes([_mux_status(), _status()], upload, _muxed(), _muxed_tree(), runner)
    assert isinstance(got["U3"], str) and got["U3"].startswith("its model's probe run failed")
    assert len(runner.decks) == 1


# --- which runs get the mux's model, against the budget (plan 008 step 2) ---


def test_a_run_with_a_mux_model_carries_its_ideal_switch_twin():
    job = _muxed_job(sc.Mux(_mux_files(), wrap=True, share=0.02))
    assert job.ideal is not None and job.ideal.ideal is None
    assert '.include "tps2121.lib"' not in job.ideal.deck
    assert any(ln.startswith("V_sw_U3") for ln in job.ideal.deck.splitlines())
    assert f"U3 (TPS2121) as an ideal switch ({sc._FOR_TIME})" in job.ideal.limits.setup
    because = sc._because(job.ideal, "a reason")
    assert "as an ideal switch (a reason)" in because.limits.setup
    # Already an ideal switch: nothing to fall back to.
    assert _muxed_job("its model didn't load").ideal is None


class TimedRunner(ProbeRunner):
    """``ProbeRunner`` on a fake clock: a run with the mux's model takes
    ``model_s``, one without it ``ideal_s``; ``timeout`` answers a model's
    run as stopped for taking too long."""

    def __init__(self, model_s: float, ideal_s: float = 50.0, timeout: bool = False) -> None:
        super().__init__()
        self.now, self.model_s, self.ideal_s, self.timeout = 0.0, model_s, ideal_s, timeout

    async def transient(self, netlist: str, files: dict[str, bytes]) -> Run | None:
        if "model probe" in netlist.splitlines()[0]:
            return await super().transient(netlist, files)
        model = "FU3 " in netlist
        self.now += self.model_s if model else self.ideal_s
        if model and self.timeout:
            self.decks.append(netlist)
            return Run(None, None, "")
        return await super().transient(netlist, files)


async def _timed(runner: TimedRunner, budget_s: float) -> list[Simulation]:
    sims = [
        s
        async for s in sc.simulate(
            [_mux_status(), _status()],
            {"tps2121.lib": MUX_LIB, "tps63802.lib": TPS_LIB},
            _muxed(),
            _reading(),
            ["startup"],
            runner,
            _muxed_tree(),
            budget_s=budget_s,
            clock=lambda: runner.now,
        )
    ]
    return sorted(sims, key=lambda s: s.id)


def _with_model(runner: TimedRunner) -> list[str]:
    """Each chain deck's corner, marked by whether the mux's model was in."""
    return [
        ("model " if "FU3 " in d else "ideal ") + d.splitlines()[0].split(" from ")[-1]
        for d in runner.decks
        if "model probe" not in d.splitlines()[0]
    ]


async def test_the_min_and_max_runs_get_the_mux_model_when_it_fits():
    runner = TimedRunner(model_s=400)
    sims = await _timed(runner, budget_s=3000)
    # nominal and bench first (800 s); min then max still fit at 400 s each.
    assert [d.split()[0] for d in _with_model(runner)] == ["model"] * 4
    assert len(sims) == 4 and all(s.verdict == "passed" for s in sims)


async def test_the_min_and_max_runs_fall_back_to_the_ideal_switch_saying_why():
    runner = TimedRunner(model_s=400)
    sims = await _timed(runner, budget_s=1000)
    # After 800 s, a 400 s model run (and the other corner's 50 s) leaves
    # 1025 s: over the 1000 s; each corner then fits as an ideal switch.
    assert [d.split()[0] for d in _with_model(runner)] == ["model", "model", "ideal", "ideal"]
    assert [s.verdict for s in sims] == ["passed"] * 4
    why = "its model's runs take"
    assert [why in s.text for s in sims] == [False, False, True, True]
    assert "more than what's left of the 16.6667 min time budget" in sims[3].text


async def test_a_mux_model_run_that_times_out_runs_again_as_an_ideal_switch():
    runner = TimedRunner(model_s=900, timeout=True)
    sims = await _timed(runner, budget_s=3000)
    marks = [d.split()[0] for d in _with_model(runner)]
    # nominal and bench time out and run again; min and max go straight to
    # the ideal switch.
    assert marks == ["model", "ideal", "model", "ideal", "ideal", "ideal"]
    assert [s.verdict for s in sims] == ["passed"] * 4
    assert "as an ideal switch (its model's run timed out after 900 s)" in sims[0].text
    assert "its model's run at another corner timed out after 900 s" in sims[2].text


async def test_the_min_and_max_runs_dont_wait_on_a_measurement_that_never_comes():
    runner = TimedRunner(model_s=400)
    # The budget is spent by nominal: bench is skipped unmeasured, and min
    # and max, waiting on a measurement, are skipped rather than left hanging.
    sims = await _timed(runner, budget_s=300)
    assert [d.split()[0] for d in _with_model(runner)] == ["model"]
    assert [s.verdict for s in sims] == ["passed", "skipped", "skipped", "skipped"]
