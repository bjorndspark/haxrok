"""Vendor-model circuit simulations: cutting a regulator's circuit out of
the netlist, the decks, the verdicts against the datasheet limits, and
running them (plan 004 step 6)."""

import dataclasses
import shutil

import pytest

from app.features.debug import spice_circuits as sc
from app.features.debug.design import Design, Node, Part
from app.features.debug.facts import DatasheetReading
from app.features.debug.schemas import (
    LimitFact,
    PowerPart,
    PowerRail,
    PowerTree,
    RailBudget,
    SpiceModelStatus,
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


def _jobs(analyses=("startup",), design: Design | None = None, **kw) -> list[sc.Job]:
    got = sc.plan(design or _orbit(), _status(), _files(), _reading(**kw), analyses)
    assert isinstance(got, list)
    return got


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
    tree: PowerTree, analyses=("startup",), design: Design | None = None
) -> list[sc.Job]:
    got = sc.plan(design or _muxed(), _status(), _files(), _reading(), analyses, tree)
    assert isinstance(got, list), got
    return got


def test_the_chain_runs_from_the_battery_through_the_fuse_and_mux():
    assert sc.chain(_muxed_tree(), "+BATT") == sc.Chain(
        ("VBAT", "/IN2", "+BATT"),
        tuple(_muxed_tree().parts[:2]),
        "battery",
        3.6,
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
    assert job.limits.setup.startswith("From USB VBUS at 5.000 V, as an ideal source through U3")


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
    assert got[0].limits.setup.startswith("From U2's output +5V at 5.000 V, as an ideal source")


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
        "Error: measure  pk_x  (MAX) : out of interval\n"
    )
    peaks = sc.parse_peaks(log)
    assert peaks["in"] == (2.880005, 5e-5) and peaks["l2"] == (1.772828, 3.7475e-4)
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
    assert [(s.id, s.ref, s.verdict) for s in sims] == [
        ("vm3", "U17", "skipped"),
        ("vm1", "U15", "passed"),
    ]
    assert "/VREG doesn't name a voltage" in sims[0].text
    assert len(runner.decks) == 2  # the load step ran but ngspice "wasn't there"

    assert [s async for s in sc.simulate(statuses, upload, design, _reading(), [], runner)] == []


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
    (sim,) = [
        s
        async for s in sc.simulate(
            [status], upload, design, reading, ["startup"], Ngspice(timeout_s=60)
        )
    ]
    assert sim.verdict == "passed", sim.text
    assert sim.t_unit == "µs" and sim.t[-1] == pytest.approx(160)
    assert sim.v[-1] == pytest.approx(3.3, abs=0.01)
    # No switch-node pin: the linear solver settings, the operating point solved.
    assert sc._LINEAR_OPTIONS in sim.deck and " uic" not in sim.deck
    assert "meas tran pk_in MAX i(vam_in)" in sim.deck
