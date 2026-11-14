# Judged issues: lpddr5-fb-no-divider

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## Translator DIR2 strapped opposite DIR1, half of each bus reversed
Verdict: real (draft)
Cites: net-(u4-dir2)|net-(u2-dir2)
Match: revers|backward|b-to-a|b to a|wrong
Why: On every SN74AXC8T245, `DIR2` (bits 5–8) is strapped through a 0R to GND, while `DIR1` is high or host-driven: `U4` passes CA bits 1–4 host to memory and bits 5–8 memory to host. The repo's later revision drops the translators.

## DMI direction lines floating
Verdict: real (draft)
Cites: /translators/dmi0_dir|/translators/dmi1_dir|/sodimm/dmi0_dir|/sodimm/dmi1_dir
Why: The edge pins DMI0_DIR/DMI1_DIR (`J1.151`, `J1.150`, and the `_A` pair) connect to nothing, and the Translators sheet's DMI0_DIR/DMI1_DIR (`R20`, `R68`, `R88`, `R90` to the `DIR2` pins) have no source: the sheet pins aren't joined.

## U9 LDO set to about 0.4 V
Verdict: wrong (draft)
Cites: net-(u9-adj)|r50|r51|r47
Why: `POT1` (20k) in series with `R51` (3k9) over `R50` (3k9) trims VOUT from 0.4 V up through 0.5 V against the LT3033's 200 mV ADJ. `R47` (0R) is a bypass option across the trimmer. The divider is unchanged in the repo's built revision.

## Translator VCCB at 0.5 V, below the 0.65 V minimum
Verdict: real (draft)
Match: 0.65
Why: Every translator's VCCB is on +0V5 (LPDDR5 VDDQ), and the SN74AXC8T245 is specified from 0.65 V. It may work, as the review says, but nothing guarantees it.

## Shared I2C has no pull-ups
Verdict: wrong (draft)
Cites: r26|r27
Match: pull
Why: The board is a SODIMM-format module; its EEPROM and PMIC hang on the host's SMBus (HSCL/HSDA), and the host carries the pull-ups, as on any memory module.

## QWIIC J3 power floating, D1 miswired
Verdict: wrong (draft)
Cites: d1|/power supply/3.3v_qwiic|j3.2
Why: `J3` lets an external controller reach the PMIC over I2C, and a QWIIC controller powers its own end; the board needs no 3.3 V there. Unchanged in the repo's built revision.

## PWR_GOOD to the host unconnected
Verdict: real (draft)
Cites: j1.7|unconnected-(j1a-pwr_good-pad7)
Why: `J1.7` (PWR_GOOD) is left open while `U9`'s PWRGD drives only the LED. The repo's next schematic commit (b032213, "improve power supply sequencing and power good signal") wires `J1.7` to PWRGD.
