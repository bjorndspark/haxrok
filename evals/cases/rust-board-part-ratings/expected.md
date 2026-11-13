Root cause (planted faults, two):

1. C15, the 4.7 uF bypass on VBUS, is a 4 V part. VBUS is 5 V on USB (up
   to 5.25 V at the source), above its rating, so the MLCC wears out and
   eventually fails short: the board that now shorts VBUS. Fix: a 16 V (or
   at least 10 V) 4.7 uF 0603, as the design had.
2. R15, the VBUS bleeder to GND, is 100 R instead of 100 k. On USB it
   dissipates (5 V)^2 / 100 R = 250 mW in an 0402 rated 62.5 mW, so it runs
   hot (the hot spot near USB-C) and draws an extra 50 mA (the idle
   current). Fix: 100 k, as the design had.

Planted edit (from the `rust-board-fb-swapped` netlist, values only): R2/R3
restored to the original 100kR/22k1R; then C15 "4.7uF 16V" -> "4.7uF 4V" and
R15 "100kR 1%" -> "100R 1%". Footprints added from the source's netlist
export so the package ratings can be read. Nets unchanged, so the ERC is
the original board's (70 messages), copied from `rust-board-fb-swapped`.
The ratings check (`ratings.py`) flags both; on the unmodified board it
lists nothing.

Source: https://github.com/esp-rs/esp-rust-board at efe1e8a,
`hardware/esp-rust-board/esp-rust-board.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if both C15 (rated below VBUS) and R15 (dissipating above its
package rating) are in the top 3 hypotheses.

Auto-check: c15|r15; rating|rated|dissipat|mw
