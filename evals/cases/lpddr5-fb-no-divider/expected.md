Root cause (real bug, fixed upstream in 4a6fea22, "schematics: fix
DCDC feedback resistors connection"):

U8 (MPM54304, a 4-channel buck module) sets each output with an
external divider on its FB pin. On all four channels the divider's
lower resistor sits from the output to GND instead of from FB to GND,
so FB connects to its output only through the upper resistor and no
current flows in it:

- FB1 (U8.18): only R33 (100k) to VOUT1; R34 (49k9) is VOUT1 to GND.
- FB2 (U8.6): only R35 (100k) to VOUT2; R36 (200k) is VOUT2 to GND.
- FB3 (U8.31): only R37 (100k) to VOUT3; R38 (130k) is VOUT3 to GND.
- FB4 (U8.24): only R39 (100k) to VOUT4; R40 (120k) is VOUT4 to GND.

FB then reads the full output, so each buck regulates at its FB
reference instead of its set point: +1V8 (VOUT1 through R43) and +1V05
(VOUT3 through R45) for the LPDDR5 (U1), and the others, all come up
low.

Fix (4a6fea22): R34, R36, R38 and R40 moved from VOUTn-GND to
FBn-GND.

The commit gives no bench observation beyond the cause, so the case has
no `symptom.md` and is scored in review mode.

Source: https://github.com/antmicro/lpddr5-testbed at 149cfc8 (the
parent of the fix 4a6fea22), `lpddr5-sodimm.kicad_sch`, unmodified.
License: Apache-2.0.

Scoring: in review mode, PASS if the FB dividers' lower resistors on
the outputs (FB tied to VOUT, no divider) are listed, as will fail or
may fail.

Auto-check: u8|mpm54304|buck|dc-dc|dcdc|fb1|fb2|fb3|fb4|vout1|vout2|vout3|vout4; fb|feedback; r34|r36|r38|r40|divider|lower resistor|bottom resistor|no current|reference
