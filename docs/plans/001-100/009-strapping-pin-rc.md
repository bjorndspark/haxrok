# Plan 009: A strapping pin's RC against the hold time

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-12 | 2026-11-12 | proposed | — | `005-whole-board-static-review.md` (step 7: MCU pin rules), `evals/cases/furby-boot-cap/` |

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
| 1 | The RC check, with the pin table's numbers | proposed |
| 2 | The same numbers from the datasheet | proposed |

## 1. The RC check, with the pin table's numbers: proposed

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

**Check:** `make lint`, `make test`, `make eval-app case=furby-boot-cap`
in debug and review mode, before and after.

## 2. The same numbers from the datasheet: proposed

- `PinRuleFact` gains the internal pull's ohms, the hold time and the
  threshold, read by the datasheet extraction, so other MCUs get the
  check and a datasheet's rules don't drop it for the ESP32-S3.
- Big: it changes the model's output schema.

**Check:** `test_debug_facts.py` for the new fields; `make lint`,
`make test`.

## Log
