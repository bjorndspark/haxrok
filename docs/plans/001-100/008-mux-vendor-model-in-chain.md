# Plan 008: The mux's own model in the power chain

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-12 | 2026-11-12 | in progress | — | `006-power-chain-simulated.md` (step 2: a mux is an ideal switch), `DECISIONS.md` ("SPICE models are untrusted text") |

Plan 006 step 2 cuts the chain from its source and turns a mux or load
switch on it into a zero-volt source, "as an ideal switch"
(`spice_circuits._cut`). The private board's U3 (TPS2121) has a vendor
model the finder already fetches (TI SLVMCO8, `data/models/lcsc-C485916/`).
It models soft start (SS, ILIM), current limit, OV/UV, reverse-current
blocking and switchover. An ideal switch shows none of them. A
switchover glitch on VDD when USB is plugged in or removed, or an inrush
current trip, can't be seen today.

A hand-built check (2026-11-12, backend container, ngspice 44.2, the
app's PSpice conversion) put TI's TPS2121 model in front of TI's TPS63070
model, using the study's 4.8 V netlist with a 33 Ω load:

| | U3 | Peak into U5.9 (L2) | Run time (0.5 ms) |
|---|---|---|---|
| L2 shorted to +3V3 | TI model | −13.98 A at 494 µs | 217 s |
| L2 fixed | TI model | −1.72 … +2.48 A (normal ripple) | 294 s |
| Study, L2 shorted | 56 mΩ switch | −14.1 A at 411 µs | — |
| U3 replaced by a 50 µs ideal ramp | — | starts normally | 83 s |

The chain converges and the fault shows as it does with the ideal
switch. Three problems block using the model as it stands:

1. **The model doesn't draw its load current from its input.** The pass
   FET's drain is `E_U_GENVIN_ABM8 DRAIN2 0 VALUE {… V(IN2)}`, a
   voltage source to ground that copies IN2. The battery supplied
   ~0.15 mA while the load drew hundreds of mA. So `Vam_in` reads
   nothing, and the bench supply's current limit (plan 006 step 4,
   `BENCH_CURRENT_LIMIT_A`) is never reached. That corner is the one
   that shows the study's 9.9 A bursts at a 0.5 A limit.
2. **About 3× the run time.** 217–294 s vs 83 s for the same 0.5 ms with
   an ideal ramp. Corners × before/after must still fit `SPICE_BUDGET_S`.
3. **Memory on long windows.** A 2 ms run of the shorted deck, written
   at 20 ns, was killed (exit 137) after 587 s.

A side finding: the study's constant-current load (`I1`, 0.1 A from
t = 0) pulled +3V3 negative before start-up, and with TI's TPS2121 in
front the TPS63070 never started in 2 ms. The app's switched-resistor
load (`Sload`) doesn't do this; keep it.

The goal: when a mux or load switch on the chain has a model that loads,
the chain runs through it, with the input current conserved; otherwise
it stays an ideal switch, labelled as today.

Acceptance:

- The private board's chain runs from VBAT through U3's TI model to U5,
  labelled as such, as built and with the fix, both within the budget.
  Their peaks into U5.9 go in the step's section as evidence; no burst
  is required (`DECISIONS.md`, "Wiring faults are judged by the wiring
  check, not a simulation's waveform").
- In that run, the input ammeter reads the load current plus the mux's
  quiescent current (within 5 %), and the bench-supply corner can reach
  its limit.
- A mux whose model fails to load or converge, or runs past its share of
  the budget, falls back to the ideal switch and says why.

Effort: about 14–22 h.

## 1. The mux's model in the cut, its input current conserved: done

- In `_cut`, a mux or load switch with a runnable model is instantiated
  with its board pins (`_model_pin`), like the regulator.
- A wrapper puts a zero-volt sense source on the mux's output and an
  F-source drawing the same current from the input the mux selected
  (IN1 or IN2, from the input voltages and PR1 as the model selects).
  It is applied only when a check run shows the model's input current
  isn't conserved. The test for that: input current below 10 % of
  output current at steady state.
- Falls back to the ideal switch, labelled with the reason, when the
  model fails to load or converge.

Decided (2026-11-12): whether a mux model needs the wrapper comes from a
short probe run per model (a fixed load, input current against output
current), cached by the model's digest in the run cache
(`spice_cache.py`). It wraps only when the input current is below 10 %
of the output current. A probe that fails to load or converge means the
ideal-switch fallback. Not "always wrap": a model that conserves current
would then draw it twice from the source.
Decided (2026-11-12, default): the wrapper and its probe are a trade-off
worth a `DECISIONS.md` entry, written in this step's commit.
Decided (2026-11-12): TI's TPS2121 model never turns on from a ramped
input (1 µs–500 µs ramps, runs to 10 ms, output under 4 mV), only from
a step. A mux model whose output stays down in the ramped probe is
probed again with its input stepped on at 0 s; if it comes up there, the
chain's source steps on at 0 s too, and the setup text says so (user's
answer at the loop's blocker).
Decided (2026-11-12, default taken in loop): behind the bench corner's
current limit such a step-only model stays an ideal switch, labelled
"its model starts only from a stepped input, and the supply's limit
ramps it" — the limit ramps the caps again, and holding the model's
input at 4.8 V with `.ic` didn't start it either (599 s, output dead).
Decided (2026-11-12, default taken in loop): the wrapper's F-source
draws from the mux's chain node (the input the cut follows), not from
an input picked by re-implementing the model's PR1 priority logic.
Step 3 (switchover) must account for a step-only model: a VBUS plug-in
ramps that input, which such a model may not follow.

**Check:** `make lint` clean (the one old eslint warning in
`button.tsx`); `make test` 432 backend + 52 frontend passed. The private
board through the app (VBAT 4.8 V, 0.49 ms window): the probe takes 3 s
and finds U3 step-only and wrapped (input current 0.14 % of output).
Nominal, fixed: 207 s, in-peak 2.77 A at 50.5 µs, `Vam_in` average
0.305 A over 0.4–0.5 ms (the ideal switch on the same deck: 0.308 A),
L1/L2 1.85 A at 208 µs, +3V3 at 1.36 V at the window's end. Nominal,
shorted: 219 s, `Vam_in` 0.313 A, L1 1.79 A at 375 µs, no L2 burst
inside 490 µs (the study's −13.98 A came at 494 µs). Bench, fixed: U3
as the ideal switch with the reason above, 58 s, input at its 1.0 A
limit from 157 µs, `Vam_in` 0.284 A. All within the budget; U5 not
reaching regulation in 490 µs happens with the ideal switch as well —
the window, step 2's subject.
Effort: 8–12 h.

## 2. Fit the budget and the window: done

- Which runs get the mux's model, measured against `SPICE_BUDGET_S` on
  the private board.
- Keep long windows from running out of memory: a coarser save step
  or fewer saved vectors when the window passes 0.5 ms.

Decided (2026-11-12): the nominal and bench-supply runs, as built and
with the fix, get the mux's model first. The min/max corners get it only
when the measured total still fits `SPICE_BUDGET_S`; otherwise they use
the ideal switch, labelled with why.

Decided (2026-11-12, default taken in loop): `simulate` measures the
runs with the model (seconds per simulated second). A min/max pair
(as built and with its fix) gets the model when the time spent plus the
pair's model runs plus every other run still queued, at its cheapest,
divided by the two parallel slots, fits `SPICE_BUDGET_S`. Otherwise it
gets the ideal switch, and the setup says why: the measured rate against
what's left of the budget, no measured run, or an earlier timeout. A run
with the model that times out (`NGSPICE_MODEL_TIMEOUT_S`) runs again as
the ideal switch while the budget lasts, labelled "its model's run timed
out after N s". Written up in `DECISIONS.md`.
Decided (2026-11-12, default taken in loop): no coarser save step and no
cut to the saved vectors. The deck already writes with `interp` at
0.25 µs and saves six vectors. A 2 ms run of the shorted chain held
ngspice at 15 MB peak RSS. The exit 137 above came from the study's
deck, which wrote every vector at 20 ns.

**Check:** `make lint` clean (the one old eslint warning in
`button.tsx`); `make test` 437 backend + 52 frontend passed. The private
board's full set through `simulate`, with the app's 900 s model timeout,
the 1800 s budget, and VBAT given an assumed 4.0–5.6 V range so that
min and max exist, took 968 s in all. Every run was flagged.

| Run | U3 | Done at | Peak L1 / L2 | Peak in |
|---|---|---|---|---|
| nominal, shorted | model | 297 s | −1.79 A / 0.00 A | 2.77 A at 50.5 µs |
| nominal, fixed | model | 299 s | −1.85 / 1.85 A at 208 µs | 2.77 A |
| bench, shorted | ideal (step-only) | 369 s | −1.78 A / 0.00 A | 1.0 A (limit) from 157 µs |
| bench, fixed | ideal (step-only) | 371 s | −1.73 / 1.73 A | 1.0 A (limit) |
| min 4.0 V, shorted | model | 667 s | −1.70 A / **17.28 A at 356 µs** | 2.77 A |
| min 4.0 V, fixed | model | 670 s | −1.73 / 1.73 A | 2.77 A |
| max 5.6 V, shorted | model | 966 s | −2.13 A / 0.00 A | 4.14 A at 2 µs |
| max 5.6 V, fixed | model | 968 s | −2.16 / 2.16 A | 4.14 A |

The as-built min corner shows the L2 burst through U3's model. A 2 ms
run of the shorted chain (nominal, through the app's deck) finished:
code 0, 1139 s, 15 MB peak RSS, flagged. L1 1.79 A at 375 µs; +3V3
reaches 3.135 V at 939 µs and settles at 3.307 V; PG rises at 1 ms.
1139 s is past the app's 900 s model timeout, so in the app a 2 ms
window would time out and run again as the ideal switch, labelled.
The timeout wasn't raised.
Effort: 3–5 h.

## 3. Switchover: open

- A run that plugs in USB (VBUS 0 → 5 V) while on the battery, and one
  that removes it, through the mux's model. The verdict reads the VDD
  dip and whether downstream regulators' PG drops.
- Only with a mux model; an ideal switch would show nothing.

Decided (2026-11-12): a switchover run is flagged when the mux's output
dips below the downstream ICs' minimum supply (the level the switched
load uses) or a downstream regulator's PG drops. The dip's depth and
length are reported either way.

Big: a new analysis the frontend labels, so it touches both sides.

**Check:** the private board's switchover runs; a unit test for the
verdict on a recorded dip.
Effort: 3–5 h.
