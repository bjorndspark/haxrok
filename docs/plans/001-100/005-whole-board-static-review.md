# Plan 005: Read the whole board and verify its wiring statically

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-11 | 2026-11-11 | In progress | — | `002-verified-wiring-checks.md`, `004-datasheets-and-simulation.md`, `DECISIONS.md` ("Evidence labels are checked references, not verified conclusions") |

A private board (an ESP32-S3 with a TPS2121 power mux feeding a TPS63070
buck-boost) was built with the TPS63070's L2 pin wired to its own output,
+3V3. The fault was found later from a bench symptom: 6 V bench supplies
needed 0.8–1.2 A to start it. A hand-built ngspice study with Claude Code
then showed what the fault does. It reduced the schematic to the power
chain (battery → TPS2121 as an ideal switch → TPS63070 → +3V3, the MCU as
a load step), ran it as built and with L2 fixed, tuned the solver for TI's
model, tried supply and load variants, and found the one signal that tells
the two apart: more than 10 A from +3V3 into U5.9 early in soft start, at
4.8 V but not with an ideal 6 V supply.

The app finds none of this today:

- `application.py` counts the inductor as present (it joins the L1 pin's
  net and the L2 pin's net, which is +3V3), so the short isn't a verified
  deviation. Whether the model reasons its way to it is luck.
- The vendor-model run cuts out the TPS63070 alone, drives it from an
  ideal 5.0 V taken from the datasheet's test conditions (VDD names no
  voltage), loads it with a resistor at the rated current and judges only
  the output voltage. Every variant of the hand-built study settles at
  3.307 V, so that verdict passes the faulty board. Its solver options
  (trapezoidal, `reltol=0.003`) are the ones the study found to fail with
  "timestep too small" at about 70.8 µs.
- The TPS2121 isn't simulated: its IN1 and IN2 are on different nets.
- Other gaps of the same kind: absolute maximums are verified only for
  pull-ups, there's no current budget, no power tree or operating states
  (USB only, battery only, switchover), and MCU pin rules (reserved and
  input-only pins) are left to the model. The eval cases have no
  switching-regulator wiring fault.

The goal: the app reads the whole board, verifies what can be verified
statically, and runs the simulations a careful engineer would, on its own,
going past the hand-built study. This plan is the static half: a power
tree and verified checks built on it, cheap and deterministic, and enough
to catch this board's fault before any simulation (steps 1–3). The
simulation half is plan 006 (below).

Effort is session time with an agent, including checks and live runs,
based on plan 004's pace (7 steps in about 1.5 days): about 20–33 h,
roughly 3–4 working days. Re-reading every cached datasheet (steps 3, 5
and 7 extend the extraction) is most of the uncertainty.

Decided (2026-11-11): split at the simulation work. This plan holds the
static checks; plan 006 holds the simulation and is written once steps 2
and 5 land, since its chain and load model build on them.
Decided (2026-11-11): work on `dev`, as plan 004 did.

## 1. An eval case where a switching regulator's inductor pin is wired to its output: done

- Plant the fault on an open-source board, as the other cases do, so the
  private board stays out of the repo. Orbit's TPS63802 (U15) is a
  buck-boost with L1/L2 pins and a TI model, so `orbit-l2-shorted`: U15's
  L2 pin moved to its VOUT net, ERC checked before and after.
- `symptom.md` from the private board's bench notes, reworded for orbit;
  `expected.md` with the edit, source and pass rule; a review-mode pass
  rule too (the fault listed as "won't work as drawn" or "may fail").
- Record today's result (`make eval-app case=orbit-l2-shorted` and
  `mode=review`) as the baseline.

Decided (2026-11-11): plant it on orbit, committed like the other cases;
the private board is checked by hand only.
Decided (2026-11-11, default taken in loop): the edit is one `+3.3V`
power symbol (#PWR060) on the inductor's L2 end, so the whole L2 net
(L1.2, U15.7) joins +3.3V, as on the private board.
Decided (2026-11-11, default taken in loop): `erc.txt` is the backend
container's KiCad 9.0.2 report, as the app would produce. It carries
library-configuration warnings the older orbit cases lack. `netlist.txt`
is the KiCad 10.0.8 export with the `_<pin>` suffix stripped from pin
functions (`L2_7` → `L2`), which makes it identical to the container's
export; `evals/README.md` now says to export with KiCad 9. Step 3's
pin-name fallback should expect that suffix from KiCad 10 boards.
Decided (2026-11-11, default taken in loop): the symptom keeps only the
private board's bench fact (start needs 0.8–1.2 A), at orbit's 1S 4.0 V.
"Keeps running at 0.5 A" and 3.30 V come from the ngspice study;
`expected.md` says so.

**Checks:** ERC before/after: 215 → 216 messages; the only new one is the
'power' library warning for #PWR060, so ERC doesn't see the fault. The
netlist diff from the unmodified board is exactly the L2 net merging
into +3.3V. Baseline (`meta/muse-spark-1.3-contributor`, one run each):

| Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|
| debug | #2 (possible miswire, unverified) | 292 s | #1 is "normal start-up inrush vs the 0.5 A limit, bench artifact"; #2 recalls that L2 must not be on VOUT and gates any rework on the datasheet's pin table. No verified finding. The printed auto-check rank (#1) is a false match on #1's claims |
| review | #1 (won't work) | 217 s | "U15 L2 shorted to VOUT - verify before any rework", from recall. #2 (may fail) is L1's 0.47 µH vs `DFE201612E-2R2M`; #3 an NRST pull-up false alarm (the STM32 has one inside) |

Both find it only by recall, unverified; the debug run ranks it below a
bench explanation. Step 3 is what makes it verified. The first review
run failed on a gateway 503 ("PII detector is unavailable"); the re-run
is the one recorded. `make lint` and `make test` pass (run as their
sub-targets, make 3.81).
Effort: 1–2 h.

## 2. A power tree: sources, rails, and which part feeds which: proposed

- From the netlist and the datasheet facts: each rail, its nominal
  voltage (named, or from `calc.py`'s feedback/fixed-output calculations),
  the part that makes it, that part's input rail, and the sources at the
  roots (USB VBUS, battery, barrel jack). Muxes and load switches are
  edges with more than one input.
- A rail with no named or calculated voltage, such as VDD here, takes its
  voltage from upstream (VBUS 5 V or the battery through the TPS2121).
- Listed in the prompt and the board summary; steps 4–6 and plan 006
  build on it.

Decided (2026-11-11): a battery's voltage range is inferred (the
charger's datasheet facts, the net name, a chemistry default such as
Li-ion 3.0–4.2 V) and shown as inferred; a field on the review form
overrides it, for a board like this one whose 4.8 V is written nowhere.
The field makes this step big (backend and frontend).

**Checks:** unit tests on small designs (LDO chain, mux with two sources,
buck-boost); orbit and the private board's trees checked by hand.
Effort: 3–5 h.

## 3. Pins the datasheet keeps apart, joined on the board (verified): proposed

- Each IC pin gets a role from its datasheet facts: supply in, supply
  out, ground, switch node, feedback, enable, signal, … (the fact
  extraction adds it; pin names like SW, LX, L1, L2 are the fallback).
- Verified issues: a switch-node pin on a rail, on ground or on another
  IC pin's net (other than the same function's repeats); a feedback pin
  straight on a rail; two pins the typical application puts on different
  nets joined on the board. `calc.py`'s EN-on-own-output check is one
  case of this and moves here if the rule covers it.
- Big: it changes the datasheet extraction's output schema, so every
  cached datasheet is read once more.

Decided (2026-11-11): roles come from the datasheet extraction, with
pin-name rules as the fallback where the extraction gives none.

**Checks:** unit tests per rule; `orbit-l2-shorted` caught as verified;
`rp-micro-no-fault` review stays PASS; the private board's U5.9 flagged.
Effort: 4–6 h.

## 4. Each pin's voltage against its datasheet limits (verified): proposed

- With the power tree's rail voltages (and their highest value across
  sources), compare every IC pin on a rail with its absolute maximum and
  recommended operating range from the facts. Today only pull-ups are
  checked (`calc.py` `_overvoltage`).

**Checks:** unit tests (over abs max, above recommended only, within);
the eval cases stay PASS.
Effort: 2–4 h.

## 5. A current budget per rail: proposed

- Each rail's load: the sum of its parts' maximum supply currents (the
  facts, with the extraction adding supply-current limits if they're
  missing), against the regulator's rated current and its input's
  limit. Over budget is "may fail", not verified, since peaks rarely
  coincide.
- The same loads become plan 006's load model.

**Checks:** unit tests; the private board's +3V3 budget checked by hand
against the ESP32-S3, MAX98357 and INMP441 datasheets.
Effort: 3–5 h.

## 6. Operating states: which rails come up from which source: proposed

- For each combination of sources (USB only, battery only, both), walk
  the power tree: which rails are up, which IC enables are reachable,
  and where a rail can be back-powered (an IO's ESD diode from a live
  rail into a dead one, a mux's reverse path). Today back-powering is
  checked through pull-ups only.

**Checks:** unit tests (a rail dead on battery, a back-powered IO);
`rust-board-i2c-vbus` still caught.
Effort: 4–6 h.

## 7. MCU pin rules from the datasheet: proposed

- Reserved pins (flash/PSRAM), input-only pins, strapping pins and their
  required levels, and pins with a fixed function (USB D+/D−), read from
  the datasheet into the facts. A pin used against its rule is verified.
  ESP32-S3 first; `reference.py`'s ESP32-C3 note stays.

**Checks:** unit tests; the private board's ESP32-S3 pins checked by
hand.
Effort: 3–5 h.

## Next: plan 006, the power chain simulated

Not part of this plan; the outline and first estimates, so they aren't
lost (about 28–46 h):

- Vendor-model runs that finish and look at currents: the study's solver
  settings for switching models (`method=gear`, relaxed tolerances,
  `interp`, `uic`, a 0.5 ms soft-start window), ammeters on switch-node
  pins and the input, PG, a reverse-current verdict; `make eval-app`
  runs vendor models (4–8 h).
- The chain from its source: the cut follows the power tree upstream,
  muxes with no usable model become labelled ideal switches, Vin from
  the source, the load from the budget as a step (6–10 h).
- Before and after a fix: a structured fix (pin → net) in the review,
  both versions run and drawn on one chart (6–10 h, big).
- Corners and tolerances: source min/nom/max, a current-limited supply,
  part tolerances, a time budget, runs cached in Redis (4–6 h).
- The model plans simulations itself through tool calls, if the gateway
  allows it and the fixed analyses leave faults uncaught (8–12 h).

Acceptance for plan 006: the private board's chain reproduces the
hand-built study (settles at about 3.31 V, bursts of more than 10 A into
U5.9 at 4.8 V, none with L2 fixed).
