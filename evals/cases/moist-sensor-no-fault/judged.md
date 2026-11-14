# Judged issues: moist-sensor-no-fault

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## MCP1702 has no voltage suffix
Verdict: wrong (draft)
Cites: u2
Match: variant|suffix|not specified|voltage version
Why: The value field reads `MCP1702` from a rescued symbol, and the rail is named `+3V3`. A missing BOM suffix is a documentation nit, not a board that fails on first power; the board is sold built.

## C6 1u on RESET slows ISP and blocks debugWIRE
Verdict: real (draft)
Cites: c6|/reset
Match: debugwire|isp|programming
Why: Microchip's AVR042 suggests at most 100nF on RESET with the 10k pull-up, and none at all for debugWIRE, which the ATtiny441 uses on this pin. `C6` is 1uF, ten times that.

## 16 MHz crystal at 3.3 V is out of spec
Verdict: real (draft)
Cites: x1
Match: 16 mhz|16mhz|out of spec|speed grade
Why: The ATtiny441 is rated to 16 MHz only from 4.5 V; at 3.3 V its safe clock is about 12 MHz. It holds only if the firmware sets the clock prescaler.

## RS485 DE shares ISP SCK
Verdict: wrong (draft)
Cites: /sck|u1.3
Match: isp|programming|program
Why: The ISP header shares the SPI pins on purpose. `R9` holds DE low in normal use, so the driver toggles only while programming on a live bus, which a sensor is not meant to be.
