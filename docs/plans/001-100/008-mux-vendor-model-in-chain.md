# Plan 008: The mux's own model in the power chain

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-12 | 2026-11-12 | open | — | `006-power-chain-simulated.md` (step 2: a mux is an ideal switch), `DECISIONS.md` ("SPICE models are untrusted text") |

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
  labelled as such. With L2 shorted, the burst into U5.9 shows; with
  the fix, it doesn't.
- In that run, the input ammeter reads the load current plus the mux's
  quiescent current (within 5 %), and the bench-supply corner can reach
  its limit.
- A mux whose model fails to load or converge, or runs past its share of
  the budget, falls back to the ideal switch and says why.

Effort: about 14–22 h.

## 1. The mux's model in the cut, its input current conserved: later

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

**Check:** unit tests for the cut with a mux model and with the
fallback; the private board's 0.5 ms chain, shorted and fixed, against
the table above, with `Vam_in` ≈ load current.
Effort: 8–12 h.

## 2. Fit the budget and the window: later

- Decide which runs get the mux's model: as-built and with-fix at the
  nominal corner, and the bench supply; or every corner. Measure them
  against `SPICE_BUDGET_S` on the private board.
- Keep long windows from running out of memory: a coarser save step
  or fewer saved vectors when the window passes 0.5 ms.

**Check:** the private board's full set of runs fits the budget; a 2 ms
run of the shorted chain finishes.
Effort: 3–5 h.

## 3. Switchover: later

- A run that plugs in USB (VBUS 0 → 5 V) while on the battery, and one
  that removes it, through the mux's model. The verdict reads the VDD
  dip and whether downstream regulators' PG drops.
- Only with a mux model; an ideal switch would show nothing.

**Check:** the private board's switchover runs; a unit test for the
verdict on a recorded dip.
Effort: 3–5 h.
