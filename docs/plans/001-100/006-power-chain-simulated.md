# Plan 006: Simulate the power chain and show the fault in its currents

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-11 | 2026-11-11 | Proposed | — | `004-datasheets-and-simulation.md`, `005-whole-board-static-review.md`, `DECISIONS.md` ("SPICE models are untrusted text") |

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

## 1. Vendor-model runs that finish and look at currents: proposed

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

**Check:** a unit test per deck change and per verdict rule; the
private board's U5 run shows the reverse-current burst (by hand);
`orbit-l2-shorted` flagged through `make eval-app`, and
`rp-micro-no-fault` not.
Effort: 4–8 h.

## 2. The chain from its source: proposed

- The cut follows the power tree upstream from the regulator to its
  sources: muxes with no usable model (the TPS2121) become labelled
  ideal switches.
- Vin comes from the source (battery range, USB 5 V), not from the
  datasheet's test conditions.
- The load comes from the rail's budget (plan 005 step 5) as a step,
  not a resistor at the rated current.

**Check:** unit tests on the chain's cut and its labels; the private
board's chain runs from its battery through U3 to U5.
Effort: 6–10 h.

## 3. Before and after a fix: proposed

- The review gives a structured fix (pin → net) for a wiring finding.
- The chain runs as built and with the fix; both are drawn on one chart
  in the UI.

**Check:** unit tests for applying a fix to the design; the private
board's before/after pair (the acceptance numbers); a frontend test of
the paired chart.
Effort: 6–10 h. Big: backend and frontend, and the review's output schema.

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

**Check:** decided when the step is.
Effort: 8–12 h.
