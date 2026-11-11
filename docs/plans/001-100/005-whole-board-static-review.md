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

## 2. A power tree: sources, rails, and which part feeds which: done

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
Decided (2026-11-11, default taken in loop): the tree is `debug/power.py`
(`power.build`), on `DesignSummary.power`. Edges are ICs with a powered
input pin and an output pin, or a switch pin through its inductor (a
regulator; a mux when there are several inputs and one output), chargers
(a BAT pin plus a charge-control pin), and pass parts: diodes, fuses,
ferrites, 0 Ω links, and slide/toggle switches. Push buttons are never a
power path. A mux lists its inputs in pin-name order (IN1 before IN2).
Decided (2026-11-11, default taken in loop): a battery's range comes from
the form's field first, then the charger's datasheet charge voltage, then
a voltage in the net name, then Li-ion 3.0–4.2 V. The field (`battery`,
"3.7" or "3.0-4.2", 0–60 V; anything else is a 400 on `battery`) is shown
and sent in review mode only. Debug runs get the inferred tree too.
Decided (2026-11-11, default taken in loop): the tree is built at upload
from the netlist and `calc.py`, then rebuilt once the datasheets are read
(output and charge voltages); the `datasheet_facts` event carries the
rebuilt tree, which replaces the summary's, so the follow-up questions get
it too. The downloaded report leaves it out, as it does the calculations.

**Checks:** 29 unit tests in `test_debug_power.py`: an LDO chain through a
fuse and a diode, the private board's shape (TPS2121 with VBUS and VBAT
into VDD, VDD 3.0–5.0 V from upstream and inferred, then the TPS63070
onto +3V3, with L2 on +3V3 adding no edge), a charger board (a slide
switch passes, a push button doesn't, a buck at its calculated 1.8 V),
voltages from datasheets, the user's battery, the prompt section and the
battery parser. Two route/stream tests cover the field (400 on a bad
value; the override reaches the summary and the review prompt), and one
frontend test covers the card's rows. orbit-l2-shorted's tree by hand:
the battery through PWR1 and the charger U1 onto +BATT (3–4.2 V,
inferred), then U15 onto +3.3V; rust-board's (D6 → VBUS → D4 → +5V → U5
→ +3V3, U6 charging +BATT) and rp-micro's too. The private board's own
project isn't on this machine, so its tree is checked only through the
unit test that mirrors it; checking it by hand is left for the review.
`make eval-app case=orbit-l2-shorted mode=review`: the L2 fault is still #1
(will fail), 311 s, against 217 s at the baseline. #2 is L1's value
against its part number, and #3 a CP2102 back-drive (may fail).
`make lint` and `make test` pass (run as their sub-targets, make 3.81):
323 backend tests, 46 frontend.
Effort: 3–5 h.

## 3. Pins the datasheet keeps apart, joined on the board (verified): done

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
Decided (2026-11-11, default taken in loop): the roles and the three rules
live in `debug/roles.py` (`roles.assign`, `roles.check`), replacing
`pins.check_wiring`; `pins.lookup` assigns every IC's roles and runs them.
A pin's role is its datasheet's first, then the pin table's inductor pins,
then its name (SW, LX, L1, L2, PH; FB; VIN, VDD, VOUT, GND, …), and the
finding cites which. A switch role from the name alone counts only on an
IC that also has a feedback or VOS pin, so a microcontroller's "SW" button
input or an analog switch's "L1" stays quiet; such a pin isn't flagged
for a missing inductor either, only for a rail or another IC on its net.
Decided (2026-11-11, default taken in loop): a feedback pin is flagged on
a rail that isn't its IC's own output (its VOUT net, the far end of its
inductor, one hop on through R, L, FB or D), and on ground only when the
role comes from a datasheet or the table and its function doesn't say to
ground it, since some fixed-output versions ground FB. A divider node is
never a rail, so a wrong divider ratio stays `calc.py`'s business.
Decided (2026-11-11, default taken in loop): a required typical-application
capacitor, inductor, diode or crystal is shorted when two of its IC pins
share a net and both have power roles (supply, ground, switch, feedback)
that differ; EN, MODE or a signal pin tied to VIN or GND is how
datasheets draw it, and FB on its own VOUT is a fixed-output version.
Decided (2026-11-11, default taken in loop): `calc.py`'s EN-on-own-output
check (`_enable_loop`) stays there: no rule here covers an enable pin, and
it already reports as a calculation the review cites.
Decided (2026-11-11, default taken in loop): the rules run at upload from
the table and names, then again once the datasheets are read; the
`datasheet_facts` event always carries the rechecked findings (`wiring`),
which replace the summary's, as the power tree does. The review prompt and
the diagrams get the rechecked ones.

**Checks:** 12 unit tests in `test_debug_roles.py`: pin-name roles (and
KiCad 10's `L2_7` names), the orbit-l2-shorted shape flagged by name with
U15's own VOUT and FB listed first and quiet when wired right, a switch
node on another IC, a correct buck (bootstrap cap, catch diode, divider)
quiet, an MCU's "SW" quiet, FB on another rail flagged and on its own
output quiet, FB on ground flagged only by a datasheet that doesn't say to
ground it, a shorted input capacitor flagged, EN tied to VIN and a
feed-forward cap on the own output quiet. Writing them found the output
hop walking back through the buck's own inductor onto its switch node;
fixed. The service test now expects the event's `wiring`. On all 8 eval
boards without datasheets (the eval path reads none, so roles there come
from the table and names): only orbit-l2-shorted's U15.7 is flagged;
HEAD's `check_wiring` flagged nothing on any of them, the TPS63802 not
being in the table. `make eval-app case=orbit-l2-shorted`: #1 VERIFIED,
citing U15.7, 221 s; `mode=review`: L2 still #1 (will fail), citing
U15.7, 245 s (311 s at step 2). `rp-micro-no-fault` review: PASS (no
issues), 86 s. The datasheet-role path is covered by the unit and service
tests only. The private board's project isn't on this machine, so U5.9
being flagged is left for the review. `make lint` and `make test` pass
(sub-targets, make 3.81): 335 backend tests, 46 frontend.
Effort: 4–6 h.

## 4. Each pin's voltage against its datasheet limits (verified): done

- With the power tree's rail voltages (and their highest value across
  sources), compare every IC pin on a rail with its absolute maximum and
  recommended operating range from the facts. Today only pull-ups are
  checked (`calc.py` `_overvoltage`).

Decided (2026-11-11): each datasheet limit lists the board pins it
applies to (or all pins), added to the extraction, so every cached
datasheet is read once more; no matching by name in the limit's text.
Decided (2026-11-11): over the absolute maximum is flagged (verified)
only when the rail's highest voltage is known (named, calculated, from a
datasheet or the user's battery field); on an inferred voltage it's
listed, not flagged, and says so. Above the recommended range but under
the absolute maximum is listed, never flagged.
Decided (2026-11-11): the pull-up check (`calc.py` `_overvoltage`)
stays; this step covers pins on rails.

Decided (2026-11-11, default taken in loop): each limit also says its
table (`kind`: absolute maximum, recommended, characteristic); only the
first two are checked. A bound relative to another pin ("VDD + 0.3 V") is
read with no number and isn't checked.
Decided (2026-11-11, default taken in loop): "known" is the power tree's
own: not `inferred` and a basis other than unknown, so USB, a fixed
regulator and rails made from known ones count.
Decided (2026-11-11, default taken in loop): below a minimum is treated
like above a maximum (below the absolute minimum flagged when known, below
the recommended one listed).
Decided (2026-11-11, default taken in loop): a pin's own limits win over
the every-pin ones, and several lines of one table count as their widest
range, so a transient rating never flags what a steady one allows.
Decided (2026-11-11, default taken in loop): results are calculations
(kind `pin_voltage`, `voltage.py`), not deviations, since deviations are
always verified and these can be listed unflagged. They're numbered after
the netlist's own and go to the prompt, the evidence check and the
diagrams' miswired pins; the `datasheet_facts` event carries them as
`calculations`, replacing the summary's.

**Checks:** 9 unit tests in `test_debug_voltage.py`: a known rail over the
absolute maximum flagged (with the bound as the expected value), an
inferred battery over it listed and not flagged, above the recommended
range only listed, below the recommended minimum listed, within (on the
bound, every-pin limits, a characteristic line) quiet, a pin's own limit
over the every-pin one, the widest line of a table and mV, unknown rails,
relative limits and ground skipped, ids after the netlist's with flagged
first. `test_debug_facts.py` checks the limit's pins filtered to the
board and the table in the fact line; the service test expects the
event's `calculations`. Writing it found the status line's text reusing
the calculations' variable name; renamed. The eval path reads no
datasheets, so it's unchanged by construction: `make eval-app
case=rp-micro-no-fault mode=review` still READ, no issues (the cached
answer, the prompt being the same). The private board's pins are left
for the review. `make lint` and `make test` pass (sub-targets, make
3.81): 344 backend tests, 46 frontend.
Effort: 2–4 h.

## 5. A current budget per rail: done

- Each rail's load: the sum of its parts' maximum supply currents (the
  facts, with the extraction adding supply-current limits if they're
  missing), against the regulator's rated current and its input's
  limit. Over budget is "may fail", not verified, since peaks rarely
  coincide.
- The same loads become plan 006's load model.

Decided (2026-11-11): the budget shows in the review prompt, the board
summary and the power-tree card in the UI (load against rated current per
rail), so the step is big.
Decided (2026-11-11): a part with no supply-current figure counts as
unknown; the rail reads "at least X mA, N parts unknown".
Decided (2026-11-11): the private board's +3V3 budget is checked by hand
at the loop's final review, since its project isn't on this machine.
Decided (2026-11-11, default taken in loop): the rating is the rated
output current of the regulator or switch making the rail; a rail made by
USB, a battery, a pass part or a charger has none. "Its input's limit" is
the input rail's own budget against its maker, since every rail carries
one.
Decided (2026-11-11, default taken in loop): a regulator draws its output
rail's load scaled by Vout max over Vin min when it steps up (lossless, so
a floor), plus its own supply current when given; a mux counts its whole
output load on each input, since either may carry it; a charger draws its
`charge_current` calc, else is unknown.
Decided (2026-11-11, default taken in loop): a rail with several makers
takes the largest rating, and only when every maker has one.
Decided (2026-11-11, default taken in loop): a supply figure naming supply
pins counts on their rail; one naming none counts on each rail of a
multi-rail IC ("whole part, counted on each of its rails"). The max
column, else the typical (noted). Unknown parts carry up to every rail
above them.
Decided (2026-11-11, default taken in loop): the budget is filled once the
datasheets are read (`budget.apply` in the review), so the first summary
and the eval path have none. The extraction prompt now asks every part
for its "supply current" and power muxes and switches for their output
current, so every cached datasheet reading misses once and is read again.

**Checks:** 7 unit tests in `test_debug_budget.py`: the mux and
buck-boost chain (the max active figure over the sleep one, U4 unknown,
the rating and its page), the load carried up stepped up ×1.10 with the
unknowns and on each mux input, over the rating as OVER BUDGET in the
prompt line, a typical-only figure reading without "at least", figures
split by supply pin against one whole-part figure on two rails, a
charger's charge current, and no budget before `apply`. Writing them
found `IDDA` missed by the supply-current pattern (a word boundary after
`IDD`); it now takes a suffix. `present.test.ts` words the budget, "at
least" and over. The eval path reads no datasheets, so it shows no
budget; the review prompt's input text changed, so `make eval-app
case=rp-micro-no-fault mode=review` ran fresh: READ, no issues (96 s).
The private board's +3V3 budget is left for the loop's final review.
`make lint` and `make test` pass (sub-targets, make 3.81): 351 backend
tests, 47 frontend.
Effort: 3–5 h.

## 6. Operating states: which rails come up from which source: done

- For each combination of sources (USB only, battery only, both), walk
  the power tree: which rails are up, which IC enables are reachable,
  and where a rail can be back-powered (an IO's ESD diode from a live
  rail into a dead one, a mux's reverse path). Today back-powering is
  checked through pull-ups only.

Decided (2026-11-11): the states show in the review prompt, the board
summary and the power-tree card in the UI (which rails are up per
state), so the step is big.
Decided (2026-11-11): a back-powered IO is "may fail", not verified,
since the netlist can't show whether the live side drives high while the
other is off. A rail that's down in some state is information, not a
finding.
Decided (2026-11-11): a state is each non-empty combination of the
tree's sources, up to 3 sources; with more, each source alone and all.

Decided (2026-11-11, default taken in loop): the states are built in
`power.build`, so the summary, the review prompt and the evals read one
result. Which rails are up is run to a fixed point; a charger never
brings its output up.
Decided (2026-11-11, default taken in loop): an enable tied to a rail,
pulled up or driven by a powered IC is on; tied to ground it holds the
part off; floating it's taken as on, with a note to check for an
internal pull. `~{EN}`, `/EN`, `nEN` and `SHDN` are active low.
Decided (2026-11-11, default taken in loop): an IC counts as powered
only when all its supply rails are up, and as dead only when all are
down, so a header symbol on two rails (the rust board's U4) is neither.
ESD back-powering is one entry per dead IC, listing its driven pins.
Decided (2026-11-11, default taken in loop): power muxes and switches
are taken to block reverse current; diodes and FETs are one-way. A FET
with drain and source on two rails is a pass part, oriented from the
battery when one side is one (the rust board's Q1 load-share was missing,
which showed +3V3 down on battery).
Decided (2026-11-11, default taken in loop): held-off and back-power
texts are the backend's English in the prompt; the UI words back-power
per locale from its rail and pins.

**Checks:** `test_debug_states.py` (9: state order, a rail dead on
battery, ESD and pull-up back-power, EN to ground, active-low and
floating EN, a GPIO-driven EN, a slide switch back-feeding +BATT, the
prompt lines, a load-sharing FET); `present.test.ts` `stateRows` (2);
the service payload carries `states`. `make lint` and `make test`
(run as their sub-targets on make 3.81): pass, 360 backend and 49
frontend tests. `make eval-app case=rust-board-i2c-vbus`: still #1,
"I2C pull-ups to VBUS, dead on LiPo - verified" (301 s); the battery
state shows +BATT, +5V and +3V3 up, VBUS down, and R17/R18 back-feeding
VBUS. `rp-micro-no-fault` in review mode: one state, no issues (188 s).

## 7. MCU pin rules from the datasheet: proposed

- Reserved pins (flash/PSRAM), input-only pins, strapping pins and their
  required levels, and pins with a fixed function (USB D+/D−), read from
  the datasheet into the facts. A pin used against its rule is verified.
  ESP32-S3 first; `reference.py`'s ESP32-C3 note stays.

Decided (2026-11-11): a strapping pin tied or pulled to a level the
datasheet says isn't normal boot is verified; one with a load but no
defined level is "may fail".
Decided (2026-11-11): the private board's ESP32-S3 pins are checked by
hand at the loop's final review, since its project isn't on this machine.

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
