# Plan 009: A strapping pin's RC against the hold time

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-12 | 2026-11-12 | done | — | `005-whole-board-static-review.md` (step 7: MCU pin rules), `evals/cases/furby-boot-cap/` |

Plan 005 step 7 reads a strapping pin's level at reset from the netlist
with capacitors ignored (`mcu._level`). That misses the bug in
`furby-boot-cap`: GPIO0 (U1.27) has C5 (100 nF) to GND, no external
pull-up, and only the module's internal pull-up (typ 45 kΩ). The chip is
off while EN is low, so the pull-up is off too and C5 stays empty. When
EN rises, the pull-up charges C5 from 0 V with τ = 4.5 ms. The chip
latches the strapping pins within the hold time after EN high (tH 3 ms,
datasheet p.14). GPIO0 is still low at that point, so the board enters
download mode on every cold boot. Today `_level` sees no pull and no
load on that net, so it says nothing.

The model found this in review (run 20261112-045722-2defa1), but only as
a hypothesis. The netlist and the datasheet settle it by arithmetic. The
pin reaches the boot level's input threshold (VIH = 0.75 × VDD) at
t = τ·ln(1/(1 − 0.75)) ≈ 1.39 τ = 6.2 ms, which is past 3 ms.

The goal: a strapping pin whose pull, internal or external, can't charge
or discharge the capacitance on its net to the boot level's threshold
within the hold time is a `mcu_pin` calculation, and it says how long the
pin takes against the hold time and what fixes it.

Acceptance:

- `furby-boot-cap`'s U1.27 is a calculation with τ ≈ 4.5 ms, a crossing
  at ≈ 6.2 ms against tH 3 ms, and the fix (a stronger pull-up, or a
  smaller C5).
- The same board with a 10 kΩ pull-up to +3V3 (τ = 1 ms, crossing at
  1.4 ms) gives no calculation.
- `make eval-app case=furby-boot-cap` (debug and review mode) is
  recorded before and after.

Effort: about 4–8 h.

| Step | What | |
|---|---|---|
| 1 | The RC check, with the pin table's numbers | done |
| 2 | The same numbers from the datasheet | done |

## 1. The RC check, with the pin table's numbers: done

- `mcu.Rule` carries a strapping pin's internal pull (way and ohms), the
  hold time, and the input threshold as a fraction of VDD. The pin
  table's ESP32-S3-WROOM-1 entry fills them for IO0 (pull-up 45 kΩ,
  VIH 0.75) and IO45 (pull-down 45 kΩ, VIL 0.25), tH 3 ms.
- `mcu._strapping` adds, for a pin whose level is right or set only by
  the internal pull: the pulls toward the boot level in parallel
  (internal and external), the capacitors from the net to the opposite
  level, τ = R × C, the crossing time t = τ·ln(1/(1 − threshold)), and
  t against the hold time.
- Tests in `test_debug_mcu.py`: the furby GPIO0 (flagged), the same with
  10 kΩ (no calculation), no capacitor (no calculation), a cap with no
  numbers on the rule (no calculation), IO45 the other way round.

Decided (2026-11-12): with the internal pull alone, a crossing past the
hold time is flagged (verified): the chip and its pull are off until EN
rises, so the cap charges from 0 V at EN high. With an external pull
toward the boot level, the same arithmetic from EN high is "may fail"
(listed): the pull runs from the rail before EN rises, and an EN delay
longer than about 5τ precharges the cap, which the netlist doesn't show.
The text says so.
Decided (2026-11-12): a datasheet's strapping rule with no RC numbers
takes them from the pin table entry that matches the same IC and pin.
Otherwise a real run, which reads the ESP32-S3 datasheet, would lose the
check until step 2. Step 2 stays, to cover other MCUs.
Decided (2026-11-12, default): the internal pull is its typical value
(45 kΩ), not its worst case, so only a clear miss is flagged. The
capacitors counted are those straight from the pin's net to the
opposite level, not behind a resistor. Pulls toward the boot level
(internal and external) combine in parallel.
Decided (2026-11-12, default): the numbers live in the pin table as
`PartPins.strap_timing` (pin -> `StrapTiming`), not on `PinRuleFact`,
which step 2 changes. `mcu.rules` attaches them to any strapping rule,
from the table or a datasheet, whose pins share one entry whose pull is
toward the rule's boot level. A load on the net with the pull alone is
"may fail" too, since the load may drive the pin during reset. The fix
names the largest added pull (beside the internal one) or the total pull,
and the largest capacitance, that meet the hold time, to 3 figures.

**Check:** `make lint`, `make test`, `make eval-app case=furby-boot-cap`
in debug and review mode, before and after.

**Checks:** six new tests in `test_debug_mcu.py` (furby GPIO0 flagged at
6.238 ms against 3 ms, with "a pull-up under 41.7 kΩ … or shrink C5 to
under 48.1 nF"; a 10 kΩ pull-up gives nothing; a 100 kΩ one is listed
with the precharge caveat; no cap or no timing gives nothing; IO45 with a
cap to +3V3 flagged; the table's timing reaching a datasheet rule, and not
one whose boot level is the other way). The real `furby-boot-cap`
netlist through `mcu.rules` + `mcu.check` gives U1.27 flagged, τ 4.5 ms,
6.24 ms vs 3 ms. Backend 443 passed, ruff/mypy clean; frontend 55
passed, lint clean. `make eval-app case=furby-boot-cap`: before, debug #1 (328 s,
"missing pull on GPIO0") and review #1 (291 s, may_fail "BOOT strapping
hold violated by C5"); after, both inputs carry the check as cf8 FLAGGED
and both answers cite it: debug #1 (333 s, "C5 + weak internal pull-up
misses 3 ms window"), review #1 with the same five items (0 s: the
gateway's answer cache, from an earlier run on the same new input).
Bugs found: hand arithmetic in the first test had the added pull-up at
41.6 kΩ; it is 1/(1/21.64k − 1/45k) = 41.7 kΩ, the code was right.

## 2. The same numbers from the datasheet: done

- `PinRuleFact` gains the internal pull's ohms, the hold time and the
  threshold, read by the datasheet extraction, so other MCUs get the
  check and a datasheet's rules don't drop it for the ESP32-S3.
- Big: it changes the model's output schema.

Decided (2026-11-12): a strapping rule's check uses the datasheet's
numbers when it gave all three (pull, threshold, hold time) with its pull
toward the boot level; otherwise the pin table's whole set, as step 1;
otherwise none. One source per check, never mixed field by field.
Decided (2026-11-12): the threshold is read only when printed as a
fraction of VDD (VIH = 0.75 × VDD); printed only in volts it is null and
that MCU gets no RC check. The model does no arithmetic on it.
Decided (2026-11-12, default): the fields are `internal_pull`
(high/low, null for none or not printed, so the pin table's own rules,
whose numbers stay in `strap_timing`, say nothing false), `pull_kohms` (typical; null when only a range is
printed), `threshold` and `hold_ms`, all required (strict output), null
when not printed.

**Check:** `test_debug_facts.py` for the new fields; `make lint`,
`make test`.

**Checks:** three new tests in `test_debug_mcu.py` (another MCU's own
numbers, 50 kΩ / 0.7 / 1 ms, flag its cap at 6.02 ms; complete datasheet
numbers win over the table's; partial numbers, or a pull the other way,
fall back to the table's whole set) and two in `test_debug_facts.py`
(`facts.check` nulls a non-positive pull or hold time and a threshold
outside (0, 1); the fact line gives "internal pull-up 45 kΩ, threshold
0.75 × VDD, hold time 3 ms"). They failed before the change (9 failures,
the schema forbade the fields). Backend 448 passed, ruff/format/mypy
clean; frontend 55 passed, eslint/tsc/prettier clean.
`make eval-app case=furby-boot-cap` with a fresh datasheet read (the
schema is in the facts cache key): the model read IO0 as pull-up 45 kΩ,
0.75, 3 ms and IO45 as pull-down 45 kΩ, 0.25, 3 ms (p.14–15), the same
numbers as the pin table, so cf8 now runs on the datasheet's own; debug
#1 (284 s, "GPIO0 held low through strapping window by C5 plus weak
pull-up", citing cf8 FLAGGED, τ 4.5 ms, 6.24 ms), review #1 (240 s,
will_fail "ESP32-S3 fails to strap for SPI boot - C5 on GPIO0", cf8).
Bugs found: none; the own diff review found nothing to fix.

## Log
