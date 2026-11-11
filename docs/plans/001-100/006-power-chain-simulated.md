# Plan 006: Simulate the power chain and show the fault in its currents

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-11 | 2026-11-11 | In progress | — | `004-datasheets-and-simulation.md`, `005-whole-board-static-review.md`, `DECISIONS.md` ("SPICE models are untrusted text") |

Plan 005 made the private board's fault (the TPS63070's L2 pin wired to
its own output, +3V3) a verified static finding. The simulation still
can't show what the fault does. A re-run of the private board after plan
005 gave the same single vendor-model run as before:

- U5 (TPS63070) alone, from an ideal 6 V ramp, into a fixed 1.65 Ω load,
  with `.options klu reltol=0.003`. It saves only `v(out)` and `v(vin)`,
  and the verdict reads only the output voltage. It came back `flagged`
  after 478 s, but on the voltage, not on what the hand-built study found.
- U3 (TPS2121) is skipped: "model has no input and output pin". The chain
  from the battery and USB through the mux isn't simulated.
- The hand-built ngspice study found the one signal that tells the faulty
  board from the fixed one: more than 10 A from +3V3 into U5.9 (L2) early
  in soft start, at 4.8 V in, none with L2 fixed. Every variant settled at
  about 3.31 V, so a voltage-only verdict can't tell them apart.
- `make eval-app` doesn't run vendor models, so no eval case checks a
  simulation.

The goal: the app simulates the power chain from its source the way the
hand-built study did, looks at currents as well as voltages, and runs the
board as built and with the fix, so the fault shows as a number. Plan
005's power tree (step 2), current budget (step 5) and operating states
(step 6) give the chain, the load and the sources.

Acceptance: the private board's chain reproduces the hand-built study:
it settles at about 3.31 V, with bursts of more than 10 A into U5.9 at
4.8 V, and none with L2 fixed.

Effort, at plan 005's pace: about 28–46 h, roughly 4–6 working days.
Most of the uncertainty is ngspice with TI's switching models: run time
and convergence (plan 004 step 6 hit "timestep too small" with them).

Decided (2026-11-11): fast-forward `main` to `dev` (plan 005 lands on
`main`), then work on `dev`, as plans 004 and 005 did.

## 1. Vendor-model runs that finish and look at currents: done

- The study's solver settings for switching models: `method=gear`,
  relaxed tolerances, `interp`, `uic`, a 0.5 ms soft-start window.
- Ammeters (zero-volt sources) on the switch-node pins and on the input;
  the PG pin probed when the part has one.
- A reverse-current verdict: current flowing into a switch-node pin from
  the output, against a limit from the datasheet.
- `make eval-app` runs the vendor models too, so `orbit-l2-shorted`
  (TPS63802) checks a simulation.

Decided (2026-11-11): build from the settings plan 005 recorded, and tune
them against the private board's run in this step; the study's own decks
stay out of the repo.
Decided (2026-11-11): the reverse-current verdict flags a burst into a
switch-node pin above the datasheet's switch current limit; without one,
above 2× the rated output current, labelled as not cited.
Decided (2026-11-11, default taken in step): a model counts as switching
when it has a switch-node pin (LX, SW, or a buck-boost's L1/L2); only then
does the deck get `method=gear reltol=0.01 abstol=1e-9 vntol=1e-5 itl4=100
interp` and `uic`, so linear models keep `klu reltol=0.003`. The window
stays the datasheet's start-up time plus the ramp (capped at 5 ms), not a
fixed 0.5 ms, so a part with a slower soft start isn't cut short; the
private runs below set their windows by hand.
Decided (2026-11-11, default taken in step): ammeters are zero-volt
sources (`Vam_in`, `Vam_<pin>`), and their peaks come from `meas tran ...
MAX` lines in ngspice's log, so the waveform file keeps only voltages.
The PG pin is probed when it sits on a net of its own, and its rise is
reported, not judged.
Decided (2026-11-11, default taken in step): `make eval-app` runs the
simulations only with `sim=startup` (or a comma list): a switching run
takes minutes, so the plain prompt eval stays quick.
Decided (2026-11-11, default taken in step): a case's datasheet URLs go
in an optional `evals/cases/<case>/parts.txt` ("REF URL" lines), standing
in for the Datasheet field the compact netlist drops, so the eval finds
datasheets and models by the app's own path.

**Checks:** 8 new or changed unit tests in `test_debug_spice_circuits.py`
(25 there): the start-up deck's input and switch-pin ammeters, labels,
switching options, `uic`, `meas` lines and PG probe; a switch current
limit read apart from an output current limit, and cited as the burst
limit; the uncited 2× limit; PG's rise and "stays low"; a burst into a
switch pin above the limit flagged while the voltages pass; the `meas`
lines parsed (a `nan` dropped); `result` reading the peaks from the log.
The toy ngspice run keeps the linear options and no `uic`.

The private board's U5 run (by hand, the app's deck at 4.8 V) finishes
with the new settings but shows **no burst**: L2's ammeter peaks at about
10 µA with L2 on +3V3, against 1.7–1.8 A from the inductor with L2 fixed.
Runs: 1.65 Ω load for 0.5 ms (94 s) and 2 ms (439 s), 16.5 Ω for 1 ms
(236 s), and 16.5 Ω with 0.5 Ω and 2 Ω in series with the input (218 s
each); the input peaks at 2.9 A of inrush at 50 µs in each. With an ideal
4.8 V source the TPS63070 stays in buck mode, and its L2 low-side FET,
the one that shorts +3V3, never turns on. The study's burst came with
the battery and the TPS2121 in front and the MCU as a load step, so
reproducing it moves to step 2 (the chain from its source) and step 4
(a current-limited supply). The output also isn't settled after 2 ms
(1.6 V at 1.65 Ω), so the acceptance's 3.31 V needs a longer window or
the study's lighter load: step 2's load from the budget.

`make eval-app case=orbit-l2-shorted sim=startup`: U15 (TPS63802)
`flagged` on the burst, **149.6 A into U15.7 (L2) on +3.3V at 578 µs**,
above the 5.75 A switch current limit (p. 6); it also settles low (2.62 V)
and PG stays low; the input peaks at 6.6 A. The prompts still rank the L2
short #1. The first run simulated nothing: a compact netlist drops the
schematic's Datasheet field, so the finders had no LCSC number, maker or
URL. A case now gives those URLs in an optional `parts.txt` (orbit's
U15 → TI's datasheet), read by `app_prompts.py`. `rp-micro-no-fault`:
READ, not flagged, but only because nothing is simulated: its one
regulator (RT9013, Richtek) has no vendor model the finder knows, so a
clean board's simulation still has no eval case.

Writing the tests found `nan` from a failed `meas` passing as a peak (now
dropped) and the waveform helper giving a column count that varied with
the probes (now fixed at five). `make lint` and `make test` pass
(sub-targets, make 3.81): 381 backend tests, 49 frontend.
Effort: 4–8 h.

## 2. The chain from its source: done

- The cut follows the power tree upstream from the regulator to its
  sources: muxes with no usable model (the TPS2121) become labelled
  ideal switches.
- Vin comes from the source (battery range, USB 5 V), not from the
  datasheet's test conditions.
- The load comes from the rail's budget (plan 005 step 5) as a step,
  not a resistor at the rated current.

Decided (2026-11-11): one chain per regulator, from the battery when one
feeds it, else USB, else an unnamed input, at its nominal: the user's
value from the review form, else the middle of its range. Step 4 adds the
min/max corners and the other sources.
Decided (2026-11-11): the load is the rail's budget current as a resistor
switched on (an ngspice voltage-controlled switch with hysteresis) once
the output passes the downstream ICs' minimum supply; with no budget
figure, the rated current, labelled.
Decided (2026-11-11, default taken in step): a mux or load switch on the
chain is a zero-volt source labelled "as an ideal switch"; a fuse,
ferrite or 0 Ω part is a labelled ideal wire, while diodes and resistors
with a value stay as they are. The chain stops at an upstream regulator's
output, which becomes an ideal source at that rail's voltage.
`load_step` and `dropout` use the budget load too, and the input ammeter
sits at the source.

**Checks:** 4 new tests in `test_debug_spice_circuits.py` (29 there):
the chain from the battery through a fuse (an ideal wire) and the mux (an
ideal switch), its title, deck nodes and setup sentence; the review form's
battery voltage and the rated-load fallback, labelled; USB when no battery
feeds the chain (and an EN driven from off the chain still skips); the
chain stopping at an upstream regulator's rail as an ideal source. The
existing start-up, burst and dropout tests now expect the switched load
(`Sload` with hysteresis at the downstream minimum) and the setup
sentence at the end of the verdict. 1 new test in `test_debug_power.py`:
a fuse from the battery into the mux feeds it one way.

The private board's chain (by hand: its 4.8 V study netlist read back
into a Design, with U5's output current and start-up time and the
ESP32-S3's supply given as facts) runs **from VBAT through U3 (TPS2121)
as an ideal switch to U5**, loaded with +3V3's 500 mA budget, in 60 s for
545 µs. It comes back `flagged` on the voltage: 1.65 V at the window's
end, PG low; the input peaks at 3.85 A at 50 µs; L2 shows **no burst**
(0.00 A), as in step 1. The ideal battery and switch hold the TPS63070
in buck mode, so the burst still waits on step 4's current-limited
supply, and the 3.31 V on a longer window.

Writing the tests found `power.build` giving a fuse between the battery
and the mux a second, reverse edge (/IN2 → VBAT), since it ranked the
fuse's own pins against themselves; it now ranks only the other parts'
edges. mypy caught two loop names reused across types in the cut (now
renamed), and the service tests' fake simulator gained the tree argument.
`make lint` and `make test` pass (sub-targets, make 3.81): 386 backend
tests, 49 frontend.

**Check:** unit tests on the chain's cut and its labels; the private
board's chain runs from its battery through U3 to U5.
Effort: 6–10 h.

## 3. Before and after a fix: done

- The review gives a structured fix (pin → net) for a wiring finding.
- The chain runs as built and with the fix; both are drawn on one chart
  in the UI.

Decided (2026-11-11): the fix comes from the backend, not the model:
`roles.check` attaches a structured fix (pin leaves a net, joins
another) to a WiringFinding when the fix is unambiguous (switch pins
first: U5.9 leaves +3V3 for the inductor's free end on a net of its
own). No prompt or output-schema change, and the with-fix run starts in
parallel with the review, as the as-built one does.
Decided (2026-11-11): the paired chart overlays the two runs' output
voltages; each run's switch-pin and input current peaks show as numbers
beside it (as built vs with the fix). The waveform stays voltages only.
Decided (2026-11-11): step 3's check records the private board's pair
(both runs finish, the fix lands on the right pins, the numbers); the
acceptance (>10 A into U5.9 as built, none with the fix) moves to
step 4's current-limited supply.
Decided (2026-11-11, default): the with-fix run happens only for a
chain a fixed finding touches, and keeps its own verdict; the pair is
linked by the with-fix run naming the as-built run's id and the finding.

`roles.apply_fix` moves the switch pin off the output onto a fresh net
(`Net-(U5-L2)`, renamed if taken) and `spice_circuits.simulate` runs each
chain a fixed finding touches a second time, as `Simulation.fix_of` /
`finding`, with its peaks as `SimPeak` rows. The UI pairs the two runs:
the with-fix output dashed over the as-built one, both verdicts, a peak
table (as built vs with the fix) and both netlists. Peaks are now signed:
ngspice measures each ammeter's `MIN` as well as its `MAX`, and the
larger magnitude is kept, worded "into" or "out of" the pin.

**Checks:** 3 new tests in `test_debug_roles.py` (the TPS63802's L2 on
its own output gets a fix to a net of its own; applying it wires the
buck-boost right; the new net never takes an existing name). 3 new in
`test_debug_spice_circuits.py`: a burst out of a switch pin is flagged
and worded "out of", a fixed finding runs its chain again with the fix
(the pair linked, both verdicts kept), and a fix elsewhere doesn't; the
deck and peak-parsing tests now expect the `MIN` lines and signed peaks.
3 new in `present.test.ts`: pairing by `fix_of`, a with-fix run whose
as-built run is missing stays on its own, and the peak rows ("840 mA"
beside "2.1 mA", the as-built label).

`evals/cases/orbit-l2-shorted` (U15, a TPS63802 with L2 on +3.3V), with
`sim=startup`, gives the pair in about 7 min: as built **passed**, 3.30 V,
3.17 A out of L1 and the input, L2 0.00 A; with wf1's fix **passed**,
3.30 V, 3.08 A through L1, L2 (on `Net-(U15-L2)`) and the input. The
private board's pair wasn't run: its project isn't on this machine, so
it is checked by hand with step 4's current-limited supply.

Bugs found: the first orbit run read L1 as "-0.00 A" while the input drew
3.17 A, since peaks were measured with `MAX` only and an outward burst is
negative. Step 2's "L2 shows no burst (0.00 A)" on the private board was
read the same way and wants rereading in step 4. Also caught: the new
`Simulation` fields first landed in the wrong TS interface (tsc), an
`&&` chain ESLint wanted optional, and a nested `if` ruff flagged
(SIM102). `make lint` and `make test` pass (sub-targets, make 3.81): 392
backend tests, 52 frontend.

**Check:** unit tests for applying a fix to the design; the private
board's before/after pair, both runs finishing with their numbers
recorded; a frontend test of the paired chart.
Effort: 6–10 h. Big: backend and frontend.

## 4. Corners and tolerances: proposed

- The source at min, nominal and max; a current-limited supply (the
  bench supplies in the private board's symptom).
- Part tolerances, a time budget for all runs in a request, and runs
  cached in Redis.

**Check:** unit tests on the corner list and the cache key; the private
board at 4.8 V and 6 V; a repeat run served from the cache.
Effort: 4–6 h.

## 5. The model plans simulations itself through tool calls: proposed

- Only if the gateway allows tool calls and steps 1–4 leave faults in the
  eval cases uncaught.

Decided (2026-11-11): kept as the last step, marked "ask first" in
`AGENTS.md`'s "Current work", so a loop stops before it and the user
decides with steps 1–4's eval results in hand.

Alternative to weigh then (not decided): expose the checks (pin table,
datasheet, reference calculations, SPICE runs) as an MCP server instead of
building a tool loop in the app. An outside agent's own model does the
round trips, so the gateway's ~90 s calls and 503s don't bound it, and the
server is a thin edge over the service functions. Against it: it needs an
MCP client (Claude Desktop, Cursor), so the web app's users don't get it,
and its runs take a different path each time, so `make eval-app` can't
measure them.

**Check:** decided when the step is.
Effort: 8–12 h.
