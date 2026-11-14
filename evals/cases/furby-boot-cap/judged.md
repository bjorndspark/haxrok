# Judged issues: furby-boot-cap

Agent judgements from the 2026-11-14 sweep, confirmed by the user in
plan 012 step 5; `make eval-score` matches each review issue against
these (format in `evals/judged.py`).

## Microphone unwired
Verdict: wrong
Cites: j7|j8|j11
Match: mic
Why: The microphone is off-board. J7, J8 and J11 are solder-wire pads (`Adhoc_SolderWire`) that carry SCK, WS and SD to it, so the board has no MK1 part to power.

## TPS63070 PS/SYNC floats with J3 open
Verdict: real
Cites: u5.1|/power management/u_vr_ps
Match: ps/sync|ps |mode pin
Why: `U5.1` (PS/SYNC) goes only to `J3.2`. With no jumper to `J3.3` (GND) or another pull, the mode input has no defined level. The TPS63070 datasheet gives it no internal pull (0.2 µA input current) and says "Pull to low for forced PWM, pull high for PWM/PFM"; it never allows it open.

## GPIO3 strap floats at reset
Verdict: wrong
Cites: u1.15
Match: gpio3
Why: On the ESP32-S3, GPIO3 selects the JTAG source only when the `STRAP_JTAG_SEL` eFuse is burnt; it is unused by default, so its level at reset doesn't matter.

## TPS3839 holds EN low for minutes
Verdict: wrong
Cites: u4
Match: 136|minute|second|delay
Why: The TPS3839G33 (`U4`) has a fixed reset delay of 200 ms typical (TI SBVS193); no part on the board sets a longer one.
