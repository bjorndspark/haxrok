# Judged issues: samd21-breakout-no-fault

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## D1/D2 short +3.3V to GND
Verdict: wrong (draft)
Cites: d1|d2
Match: short|clamp|across|collapse
Why: The netlist's pin names (1 K, 2 A, 3 K) are the KiCad symbol's. On the BAV99, pin 3 is the common node, so `D1` and `D2` clamp `/USB+` and `/USB-` to +3.3V and GND, and neither conducts from +3.3V to GND.

## D3 backwards, VBUS can't power the board
Verdict: wrong (draft)
Cites: d3
Match: backward|block|cannot|back-feed|back-drive|revers
Why: Same BAV99 pinout: `D3` conducts from VBUS to +5V and blocks +5V from reaching the host.

## Secondary I2C pairs have no pull-ups
Verdict: wrong (draft)
Cites: pa08{slash}~d2{slash}sda2|pa16{slash}~d11{slash}sda3|u1.13|u1.25
Match: pull-up|pullup
Why: SDA2/SCL2 and SDA3/SCL3 are alternate functions of header GPIO pins (D2, D3, D11, D13 with the LED). Fixed pull-ups would load them as GPIO; a breakout leaves them to the attached device, as the Arduino MKR boards do.

## RESET has no pull-up
Verdict: wrong (draft)
Cites: ~{reset}|u1.40|sw1
Match: pull-up|pullup|float
Why: The SAMD21's RESET pin has an internal pull-up; with the 100nF and the button through 470R it needs no external one.

## AMS1117 output capacitor too small for stability
Verdict: real (draft)
Cites: c30|u2.2|u2
Match: output cap|compensation|oscillat|esr|stabil
Why: `C30` is a 10uF with no type. The AMS1117 datasheet asks for 22uF solid tantalum on the output for stability over all conditions, and a low-ESR ceramic can make this regulator ring. It runs on the bench, so it rates "may fail", not an error.
