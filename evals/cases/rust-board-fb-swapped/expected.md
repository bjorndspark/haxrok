Root cause (planted fault):

The SY8088 (U5) feedback divider has its two resistors swapped. R2 (+3V3 to
FB) is 22k1 and R3 (FB to GND) is 100k; the design needs R2 = 100k on top and
R3 = 22k1 on the bottom.

The SY8088 regulates FB to 0.6 V, so VOUT = 0.6 V x (1 + R2/R3). With the
values swapped, VOUT = 0.6 x (1 + 22.1/100) = 0.73 V instead of
0.6 x (1 + 100/22.1) = 3.31 V. Everything on +3V3 (ESP32-C3, sensors,
WS2812) is unpowered. The charger runs from VBUS, so it still works.

Fix: swap R2 and R3 (R2 = 100k, R3 = 22k1). On the bench, swap the two
0402 parts.

Planted edit (`esp-rust-board.kicad_sch`): the Value and LCSC fields of R2
and R3 swapped, both on the symbols and in `symbol_instances`
(R2 100kR/C25741 -> 22k1R/C43473; R3 22k1R/C43473 -> 100kR/C25741).
ERC before and after: identical (70 messages).

Source: https://github.com/esp-rs/esp-rust-board at efe1e8a,
`hardware/esp-rust-board/esp-rust-board.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if the swapped feedback divider (wrong R2/R3 ratio setting
+3V3 to ~0.73 V) is in the top 3 hypotheses.

Auto-check: r2|r3; fb|feedback; divider|ratio
