"""Vendor-model circuit simulations: cutting a regulator's circuit out of
the netlist, the decks, the verdicts against the datasheet limits, and
running them (plan 004 step 6)."""

import dataclasses
import shutil

import pytest

from app.features.debug import spice_circuits as sc
from app.features.debug.design import Design, Node, Part
from app.features.debug.facts import DatasheetReading
from app.features.debug.schemas import LimitFact, SpiceModelStatus
from app.features.debug.spice import Ngspice, Run
from app.features.debug.spice_convert import ModelFiles

needs_ngspice = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")

TPS_PINS = ["AGND", "EN", "FB", "GND", "L1", "L2", "MODE", "PG", "VIN", "VOUT"]
TPS_LIB = b".SUBCKT TPS63802 AGND EN FB GND L1 L2 MODE PG VIN VOUT\nR1 VIN VOUT 1\n.ENDS\n"


def _fact(parameter: str, mn=None, typ=None, mx=None, unit="V", conditions="") -> LimitFact:
    return LimitFact(
        parameter=parameter, min=mn, typ=typ, max=mx, unit=unit, conditions=conditions, page=1
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


def test_the_startup_deck_ramps_the_input_and_loads_the_rated_current():
    (job,) = _jobs()
    deck = job.deck
    assert job.ref == "U15" and job.analysis == "startup"
    assert job.title == "U15 (TPS63802) start-up, vendor model"
    assert job.files == {"tps63802.lib": TPS_LIB}
    lines = deck.splitlines()
    assert lines[0] == job.title
    assert '.include "tps63802.lib"' in lines
    assert "Vin vin 0 PWL(0 0 5e-05 3.6)" in lines  # the datasheet's typical input
    assert "Rload out 0 1.65" in lines  # 3.3 V at the rated 2 A
    assert "XU15 0 vin n1 0 n2 n3 0 n4 vin out TPS63802" in lines
    for part in ("R7 out n1 1e+06", "R8 n1 0 100000", "L1 n2 n3 4.7e-07", "C13 out 0 2.2e-05"):
        assert part in lines
    assert "R6 out n4 100000" in lines  # the PG pull-up
    assert "C14 vin 0 1e-05" in lines
    # The MCU is the load resistor; its reset RC is kept and probed.
    assert "R9 out rst 10000" in lines and "C9 rst 0 1e-07" in lines
    assert not any(ln.startswith("XU16") for ln in lines)
    assert "Rleak_out out 0 1G" in lines and "Rleak_rst rst 0 1G" in lines
    assert "save v(out) v(vin) v(rst)" in lines
    assert "tran 2.5e-07 0.0006495" in lines  # the ramp, then 1.1 × (224 + 321) µs
    assert "wrdata out.txt v(out) v(vin) v(rst)" in lines
    assert job.limits.reset == "U16.7 (NRST)"
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
    assert f"Vin vin 0 PWL(0 0 5e-05 3.6 0.0006495 3.6 0.0011495 {vend:g})" in drop.deck
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
    assert skip(reading=no_current) == "the datasheet facts give no rated output current"
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


def test_a_model_without_input_and_output_pins_isnt_a_regulator():
    files = _files(["A", "B", "GND"])
    assert sc.plan(_orbit(), _status(), files, _reading(), ["startup"]) is None


def test_numbered_model_pins_meet_the_boards_repeated_pin_names():
    # TPS63070's model has VIN_0, VIN_1, VOUT_0, VOUT_1; its symbol says VIN, VOUT.
    pins = [*TPS_PINS[:-2], "VIN_0", "VIN_1", "VOUT_0", "VOUT_1"]
    got = sc.plan(_orbit(), _status(), _files(pins), _reading(), ["startup"])
    assert isinstance(got, list)
    assert "XU15 0 vin n1 0 n2 n3 0 n4 vin vin out out TPS63802" in got[0].deck.splitlines()


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
    stop: float, out, vin=lambda t: 3.6, rst=None, step: float = 5e-6
) -> list[tuple[float, ...]]:
    n = round(stop / step)
    rows = []
    for i in range(n + 1):
        t = i * step
        rows.append((t, out(t), vin(t)) + ((rst(t),) if rst else ()))
    return rows


def _ramp(t: float, rise: float = 200e-6, top: float = 3.3) -> float:
    return min(t / rise, 1.0) * top


def test_a_clean_startup_passes():
    (job,) = _jobs()
    rows = _rows(649.5e-6, _ramp, rst=lambda t: _ramp(t, 500e-6))
    got, text = sc.verdict("startup", job.limits, rows)
    assert got == "passed"
    assert text.startswith("Reaches 3.267 V at ")
    assert "settles at 3.300 V" in text and "ceiling 3.600 V (U16 supply max)" in text


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
        "The datasheet's dropout puts it near 3.500 V."
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
