# Judged issues: rust-board-fb-swapped

Agent drafts from the 2026-11-14 sweep and plan 012 step 1;
`make eval-score` matches each review issue against these (format in
`evals/judged.py`). The four rust-board cases share one board, so their
entries are the same.

## GPIO2 strap has no pull-up
Verdict: real (draft)
Cites: io2|u3.5|r11
Match: gpio2
Why: GPIO2 must read 1 at reset for SPI boot, and the ESP32-C3 hardware guide asks for an external pull-up. `IO2` goes only through `R11` (0R) to the WS2812's DIN and to header `U4.7`, and neither one drives it. Whether the board boots reliably on the bench hasn't been checked.

## GPIO8 strap shared with I2C SCL
Verdict: wrong (draft)
Cites: io8_scl|u3.22
Match: gpio8
Why: `R17` (10k) pulls `IO8_SCL` up to `+3V3`, which is the level the strap needs. A sensor doesn't hold SCL low at reset.

## ENABLE pulled up to 5V at a header pin
Verdict: wrong (draft)
Cites: enable|u4.27
Match: enable
Why: `U4` is the board's edge-header symbol, not a 3.3V chip. `R1` (10k) pulls `ENABLE` to `+5V` for the buck's EN (`U5.1`), and `U4.27` brings it out so a user can pull it low to switch the buck off.

## Buck can't hold 3.3V at low battery
Verdict: real (draft)
Cites: u5.4|q1|+batt
Match: buck; sag|drop out|dropout|low batt|cutoff
Why: On battery, `+5V` is VBATT through `Q1`, and `U5` is a buck. As a Li-ion battery runs down toward 3.3V, the buck drops out and `+3V3` sags.

## Two Schottkys in series from VBUS
Verdict: real (draft)
Cites: d6|d4
Match: schottky|diode|drop
Why: USB VBUS passes through both `D6` and `D4` (B5819W) to reach `+5V`, so two diode drops come off the rail.

## 32k crystal fitted with load caps and R12 DNP
Verdict: real (draft)
Cites: y1|c12|c13|r12
Match: 32
Why: `Y1` is fitted, while `C12`, `C13` and `R12` are DNP. If firmware selects the external 32k clock, it may not start or may run off frequency.
