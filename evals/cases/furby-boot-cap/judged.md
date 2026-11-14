# Judged issues: furby-boot-cap

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## Microphone unwired
Verdict: wrong (draft)
Cites: j7|j8|j11
Match: mic
Why: The microphone is off-board. J7, J8 and J11 are solder-wire pads (`Adhoc_SolderWire`) that carry SCK, WS and SD to it, so the board has no MK1 part to power.

## TPS63070 PS/SYNC floats with J3 open
Verdict: real (draft)
Cites: u5.1|/power management/u_vr_ps
Match: ps/sync|ps |mode pin
Why: `U5.1` (PS/SYNC) goes only to `J3.2`. With no jumper to `J3.3` (GND) or another pull, the mode input has no defined level. The datasheet text on an open PS/SYNC is not checked yet.

## GPIO3 strap floats at reset
Verdict: wrong (draft)
Cites: u1.15
Match: gpio3
Why: On the ESP32-S3, GPIO3 selects the JTAG source only when the `STRAP_JTAG_SEL` eFuse is burnt; it is unused by default, so its level at reset doesn't matter.
