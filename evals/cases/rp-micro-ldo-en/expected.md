Root cause (planted fault):

The RT9013-33 (U3) enable pin EN (U3.3) is tied to its own output, +3V3
(U3.5 VOUT), instead of to VIN (RAW). At power-up VOUT is 0 V, so EN is
low and the LDO stays off; it can never start itself. Once something else
raises +3V3 above the EN threshold, EN goes high, the LDO turns on and then
holds its own enable high: the board latches on until RAW is removed.

Fix: tie EN to VIN (RAW). On the bench, lift U3 pin 3 (or cut its trace to
+3V3) and bridge it to pin 1.

Planted edit (`rp-micro.kicad_sch`): removed the wire from EN (241.3, 45.72)
up to the VIN wire, and the junction at (241.3, 40.64); added a +3V3 power
symbol (#PWR033) at the EN wire end (241.3, 45.72), with its
`symbol_instances` entry. ERC before and after: 40 -> 41 messages; the only
new one is a "symbol modified in library" warning for the added +3V3
symbol.

Source: https://github.com/siderakb/rp-micro at a7baf36,
`hardware/rp-micro.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if the LDO enable tied to its own output (+3V3 instead of
RAW/VIN) is in the top 3 hypotheses.

Auto-check: en; u3|rt9013|ldo|regulator; +3v3|vout|output
