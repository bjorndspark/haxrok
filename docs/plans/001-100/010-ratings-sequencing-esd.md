# Plan 010: Part ratings, rail sequencing and connector ESD

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-14 | 2026-11-14 | done | — | `005-whole-board-static-review.md` (power tree, pin limits, operating states), `006-power-chain-simulated.md` (step 4: DC-bias as a corner) |

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

- Each check flags or lists its planted eval case and adds no flagged
  finding on the fixed variant or on `rp-micro-no-fault`. The ESD check
  lists rp-micro's and orbit's real unprotected USB lines as may fail;
  `rp-micro-no-fault`'s debug answer still must not rank them.
- `make eval-app` on each new case (debug and review mode) is recorded:
  the answer ranks the planted fault and cites the calculation.
- No new false alarms on the existing cases (a `make eval-app` sweep, or
  `make replay` on recorded runs where that suffices).

Effort: about 1–2 days, a step per session.

| Step | What | |
|---|---|---|
| 1 | A capacitor's voltage rating and a resistor's power against the board | done |
| 2 | Rail order against the order a datasheet requires | done |
| 3 | External connector lines with no ESD protection | done |

Decided (2026-11-14): the plan covers these three checks: part ratings,
rail sequencing and connector ESD. Absolute max, back-powering, I²C
pull-ups and strapping timing are already covered (plans 002, 005, 009).
The review workflow (revision diffs, waivers) is a later plan.

## 1. A capacitor's voltage rating and a resistor's power against the board: done

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

Built (2026-11-14): `ratings.py` (kind `part_rating`), run in
`context.build` after the MCU check. A rating also comes from a `Rated
Voltage` / `Power` field variant. The voltage across a part is the
highest on one end less the lowest on the other, both ends on ground or
a power-tree rail. Over the rating on an inferred rail is listed, not
flagged. Resistors take the same 80 % "may fail" line as capacitors (no
decision covered it). An LED's resistor uses I²R from its `led_current`.
The new case `rust-board-part-ratings` is the original rust-board
(R2/R3 unswapped) with C15 4.7uF 16V → 4V and R15 100kR → 100R. Its
netlist carries footprints, which `parse_compact` now reads (furby's
too, so furby's prompt now shows them).

**Checks:** `test_debug_ratings.py`, 15 tests. On the new case the check
flags C15 (125 % of 4 V) and R15 (250 mW, 400 % of an 0402's 62.5 mW).
On the original board, `rp-micro-no-fault` and every other case it lists
nothing. `make eval-app case=rust-board-part-ratings`: debug #1 R15, #2
C15, citing cf8 and cf7 (343 s); review #1 C15 (may_fail), #2 R15
(will_fail), the same citations (290 s). `make lint` clean apart from
an existing ESLint warning in `shared/ui/button.tsx`, which this step
didn't touch; `make test` 463 backend + 55 frontend passed. Bugs found: ruff
SIM114 in `check`'s loop, rewritten.

## 2. Rail order against the order a datasheet requires: done

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
Decided (2026-11-14): the eval case is `orbit-vdda-late`, from orbit.
The STM32F030's datasheet requires VDDA up before or with VDD. The
planted edit moves U16's VDDA off +3.3V onto an added RT9013-33 LDO
(the part rp-micro already uses), fed from +BATT with its EN on +3.3V,
so the wiring brings VDDA up after VDD (flagged). The unmodified orbit
board is the fixed variant and must stay silent.
Decided (2026-11-14): only the order is checked. A time limit in the
datasheet ("within 10 ms of VDD") is quoted in the finding's text for
the reviewer to measure, not computed; the netlist can't show
regulator start-up times.
Decided (2026-11-14), defaults: the check lives in `sequencing.py`
with calculation kind `rail_order`; `DatasheetFacts` gets a
`sequencing` list (the rails by pin, the relation, any time limit, the
page).

**Check:** `test_debug_facts.py` for the new fields; unit tests for the
order check; `make eval-app case=<new case>` with a fresh datasheet read;
`make lint`, `make test`.

Built (2026-11-14): `sequencing.py` (kind `rail_order`), run in
`context.build` after the ratings check. Each side of a requirement is
the rail its pins are on; rails joined only by pass parts count as the
rail they're fed from. A rail is up no later than another when it feeds
the other's maker, sits on its enable (tied, pulled up, or a power-good
pin of the regulator that makes it), or supplies the IC driving that
enable, and so on down the chain; a rail with two makers counts only
what both wait for. `states.py`'s enable and pull-resistor helpers are
now public (`enable_pins`, `resistors_on`) for it. `pdf.py` sends pages
that mention sequencing. The new case `orbit-vdda-late` is orbit with
U16's VDDA moved onto an added RT9013-33 (U23) fed from +BATT with its
EN on +3.3V; ERC 208 → 218, all library-configuration warnings and
+BATT's one undriven-input error moving to U23.1.

**Checks:** `test_debug_sequencing.py`, 10 tests; `test_debug_facts.py`
for the new field. Fed the STM32's VDDA-first fact by hand, the check
flags the new case and lists nothing on the unmodified orbit board or
`rp-micro-no-fault`. `make eval-app case=orbit-vdda-late` with a fresh
read (the facts cache key holds the schema): U16's p.13 gave the
VDDA-first fact (ds73) and the check flagged it as cf4; debug #1 "VDD up before VDDA: U23 EN from +3.3V", citing
cf4 (289 s); review #2 may_fail, citing cf4 (281 s), behind the
board's own TPS63802 inductor value. `make lint` clean apart from the
existing ESLint warning in `shared/ui/button.tsx`; `make test` 475
backend + 55 frontend passed. Bugs found: none in the diff review
beyond an overlong docstring line in `context.build`, rewrapped.

## 3. External connector lines with no ESD protection: done

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
Decided (2026-11-14): rp-micro's USB-C (through 27 Ω series resistors
only) and orbit's USB are really unprotected, so the check lists them
as may fail on every rp-micro and orbit case, `rp-micro-no-fault`
included. The acceptance line now asks for no new flagged finding
there, and the prompt tells debug mode that a missing TVS is a design
risk, not a cause, unless the symptom fits ESD damage.
Decided (2026-11-14), defaults: the check lives in `esd.py` with
calculation kind `connector_esd`; the eval case is
`rust-board-usb-no-esd`, the original rust-board with the D+/D− TVS
diodes D5 and D8 removed. The unmodified board is the fixed variant.

**Check:** unit tests (protected, unprotected, a series resistor alone,
power pins ignored); `make eval-app case=<new case>`; `make lint`,
`make test`.

Built (2026-11-14): `esd.py` (kind `connector_esd`, never flagged), run
in `context.build` after the sequencing check. A connector is a J, P,
CN, CON or USB designator or a `Connector…` symbol; test points are
not, even in KiCad's Connector library. Each signal pin (not ground, a
power-tree rail, or a supply or shield pin) is followed through series
parts (R, FB, L, F, JP, FL) to IC pins; a walk never enters ground or a
rail, so a pull-up isn't a path. A line is protected by a part named as
a TVS or ESD array (D_TVS, Power_Protection, USBLC, TPD…, PESD…) or a
non-LED diode to ground or a rail anywhere on that path. External kinds
get one may-fail calculation per connector, with the IEC 61000-4-2 vs
HBM point, a USBLC6-2 fix, and the chip's own ESD fact quoted when the
datasheets give one. A header gets one context calculation listing up
to 6 lines. The prompt's verified-inputs paragraph says how to weigh
them (a cause only when the symptom fits ESD damage; may fail at most
in a review). The new case `rust-board-usb-no-esd` is the original
rust-board with D5 and D8 removed from the netlist; its ERC is the
original's, which names neither.

**Checks:** `test_debug_esd.py`, 12 tests. Over every case the check
lists J1 on `rust-board-usb-no-esd`, USB1 on the orbit cases and J1
(through R1/R2) on the rp-micro cases as may fail, and nothing for the
USB on the other rust-board cases (D5/D8) or furby (USBLC6-2); headers
(orbit J1/J2, rp-micro J2–J4, furby J3–J16) are context. `make eval-app
case=rust-board-usb-no-esd`: debug #1 "ESD damage to U3 USB_D+ pin",
citing cf7 (222 s); review #2 may_fail "User USB D+/D- have no ESD",
citing cf7 (175 s), behind GPIO2's strapping pull-up (the original
board's). `make eval-app case=rp-micro-no-fault` (debug): READ, #1
charge-only cable as before; the answer cites the USB line (cf2) and
rules it out ("never worked fresh, so not cause") (284 s). `make lint`
clean; `make test` 487 backend + 55 frontend passed. Bugs found: test
points (TP*) listed as headers in the first probe, now excluded; two
E501 lines in `esd.py`, rewrapped; an unused argument to
`_chip_ratings`, dropped.

## Log
