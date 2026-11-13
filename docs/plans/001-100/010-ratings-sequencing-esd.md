# Plan 010: Part ratings, rail sequencing and connector ESD

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-14 | 2026-11-14 | proposed | — | `005-whole-board-static-review.md` (power tree, pin limits, operating states), `006-power-chain-simulated.md` (step 4: DC-bias as a corner) |

The review now covers most of what a senior reviewer checks on a
schematic: absolute maximums per pin in every operating state (plan 005
steps 4 and 6), back-powering through ESD diodes and pull-ups (plan 005
step 6), I²C pull-ups and rise time (plan 002, `spice.py`), output
capacitance against the datasheet's application circuit after DC-bias
derating (`application.py`), and strapping and EN/RESET RC timing (plans
002 and 009). Three checks that a reviewer runs on most boards are still
missing:

- **Part ratings.** A 6.3 V capacitor on a 5 V rail that a USB source
  can push higher, a 10 V capacitor on a 12 V input, or a 0402 resistor
  dissipating 200 mW. These failures can take months to show up and
  are cheap to catch. The netlist and the power tree already give each
  net's highest voltage (`voltage.py`).
- **Rail sequencing.** Some ICs need one rail up before another (core
  before I/O, an analog supply before a digital one) or within a time of
  it. The power tree and enables (`power.py`, `states.py`) show which
  rails come up together and from what. Nothing compares that with the
  order a datasheet requires.
- **Connector ESD.** Signal lines from an external connector (USB data,
  headers that leave the box, antennas) with no TVS or ESD part between
  the connector and an IC pin. This is common on hobby boards and only
  shows up as field failures.

The goal: each of the three is a backend calculation the model cites,
the same way as the existing checks, with an eval case that has the
fault planted, and a version of that board without it.

Acceptance:

- Each check flags its planted eval case and stays silent on the fixed
  variant and on `rp-micro-no-fault`.
- `make eval-app` on each new case (debug and review mode) is recorded:
  the answer ranks the planted fault and cites the calculation.
- No new false alarms on the existing cases (a `make eval-app` sweep, or
  `make replay` on recorded runs where that suffices).

Effort: about 1–2 days, a step per session.

| Step | What | |
|---|---|---|
| 1 | A capacitor's voltage rating and a resistor's power against the board | proposed |
| 2 | Rail order against the order a datasheet requires | proposed |
| 3 | External connector lines with no ESD protection | proposed |

Decided (2026-11-14): the plan covers these three checks: part ratings,
rail sequencing and connector ESD. Absolute max, back-powering, I²C
pull-ups and strapping timing are already covered (plans 002, 005, 009).
The review workflow (revision diffs, waivers) is a later plan.

## 1. A capacitor's voltage rating and a resistor's power against the board: proposed

- An eval case with a capacitor rated below its rail's highest voltage
  (a 6.3 V MLCC on VBUS, say), and a resistor dissipating above its
  package's rating.
- Capacitor: rated voltage against the net's highest voltage from the
  power tree (`voltage.py`'s range), and against a derating margin.
- Resistor: P = V²/R across it, or I²R where a current is known (the
  rail budget, `budget.py`, an LED's current), against the package's
  rating.

Decided (2026-11-14): ratings come from the schematic alone, with no
model call. A capacitor's voltage comes from its value ("10uF 16V") or a
`Voltage` field. A resistor's power comes from a `Power` field, otherwise
the usual rating for its package size from the footprint (0402 62.5 mW,
0603 100 mW, 0805 125 mW, 1206 250 mW). Reading MPN datasheets for
passives is later, once the static check has proved itself. (The eval
netlists have a rating on some capacitor values and none on most.)
Decided (2026-11-14): a net whose highest voltage is above a
capacitor's rating is flagged (verified). Above 80 % of the rating, the
capacitor is listed as "may fail" (a common derating rule, and MLCCs lose
much of their capacitance near the rating). A 6.3 V capacitor on 5 V
(79 %) passes; on a VBUS that can reach 5.5 V (87 %) it's listed.
Decided (2026-11-14): a capacitor with no rating found is listed
("rating unknown, check it") only on a net that reaches 12 V or more,
where a default 10–16 V MLCC can fail; below that it's ignored, so a
typical 3.3/5 V board stays quiet. A resistor always has a rating once
its footprint is known; with neither a `Power` field nor a known
package, it's ignored.

**Check:** unit tests for each rule (over, within, at the margin, no
rating); `make eval-app case=<new case>`; `make lint`, `make test`.

## 2. Rail order against the order a datasheet requires: proposed

- The datasheet extraction (`facts.py`, `datasheet.py`) reads each IC's
  sequencing requirement: which supply must be up before which, and any
  time between them.
- A check compares that with the order the power tree and enables give:
  rails from one regulator's outputs, an enable driven from another
  rail, a power-good chained to an EN.
- An eval case with an IC whose core and I/O rails come up in the wrong
  order.
- Big: it changes the model's output schema.

Decided (2026-11-14): flagged (verified) only when the wiring fixes the
wrong order, e.g. a PG→EN chain that brings I/O up before core. When
the order rests on start-up times the netlist can't show (two
regulators enabled from the same source), it's listed as "may fail"
with the requirement and both rails, so the reviewer checks it on a
scope.

**Check:** `test_debug_facts.py` for the new fields; unit tests for the
order check; `make eval-app case=<new case>` with a fresh datasheet read;
`make lint`, `make test`.

## 3. External connector lines with no ESD protection: proposed

- Find connectors (`J*`, `P*`, `USB*`, a connector footprint) whose
  pins reach an IC pin, and the parts on that path: a TVS, an ESD array,
  a series resistor, a common-mode choke.
- An unprotected data line is listed as "may fail", with the connector
  pin and the IC pin.
- An eval case with a USB connector wired straight to an MCU's D+/D−.

Decided (2026-11-14): known external kinds (USB, RJ45, SMA/U.FL,
barrel, audio, SD/SIM), recognized by footprint or symbol, are checked
by the backend. Other headers' unprotected lines go to the model as
context, not as findings. The model judges from names and labels
("DEBUG", "EXT_IO") whether they leave the box, and reports them only
as "may fail".
Decided (2026-11-14): an IC's own ESD protection doesn't drop the
finding. On-chip HBM protection (about 2 kV) covers handling in the
factory, not a user touching a port (IEC 61000-4-2, 8 kV contact). The
line is still listed as "may fail", and the text mentions the chip's
own rating when the datasheet facts give one.

**Check:** unit tests (protected, unprotected, a series resistor alone,
power pins ignored); `make eval-app case=<new case>`; `make lint`,
`make test`.

## Log
