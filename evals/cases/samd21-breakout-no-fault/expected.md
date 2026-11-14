Root cause: none in the schematic. This is the unmodified board, a
review-only case: it has no `symptom.md`, and `make eval-score` reviews
it for false alarms.

The board: an ATSAMD21G18A breakout laid out like the Arduino MKR Zero.
Micro-USB VBUS -> one BAV99 diode (D3) -> +5V -> AMS1117-3.3 (U2, 10uF
in and out) -> +3.3V, or 3.3 V straight into the +3.3V pin. VDDANA is
fed from +3.3V through a 10uH filter inductor (L1) with 100nF and 10uF;
VDDCORE has the 1uF the internal regulator needs. USB D+/D- go straight
to PA24/PA25 (the SAMD21's USB pads have their series termination
built in), each clamped by a BAV99 (D1, D2) to +3.3V and GND. 32.768 kHz
crystal (Y1) with 6.8pF caps. RESET has 100nF and a button through
470R; the SAMD21 has an internal pull-up on RESET. 4k7 I2C pull-ups on
PA22/PA23, SWD header, power LEDs on +5V and +3.3V.

What a reviewer might wrongly flag:
- No 22-27R series resistors on USB D+/D-: the SAMD21 needs none.
- No external pull-up on RESET: the internal one is enough with the
  100nF and the button.
- Y1's 6.8pF load caps: right for a crystal with CL around 7-9pF; the
  schematic doesn't name the crystal.
- D3 being half of a BAV99: it is the VBUS reverse-current diode, which
  the KiCad symbol draws as a BAV99; its current rating is a fair
  question, not a wiring error.
- D1-D3 "reversed": the netlist's pin names (1 K, 2 A, 3 K) are the
  KiCad symbol's. On the BAV99, pin 1 is an anode, pin 2 a cathode and
  pin 3 the common node, so D1 and D2 clamp each USB line to +3.3V and
  GND, and D3 conducts from VBUS to +5V.
- No pull-ups on SDA2/SCL2 and SDA3/SCL3: they are alternate functions
  of header GPIO pins, left to the attached device.
- USB ID, J2's SWO/TDI and the EDBG/LED pins unconnected: unused on
  this board.

Source: https://github.com/atoomnetmarc/SAMD21-breakout at 464fac8,
`SAMD21 breakout.kicad_sch`, unmodified. License: Apache-2.0.

Scoring: PASS if the review calls no correct part of the design an
error. Each issue it raises is judged in `judged.md`.

Auto-check: none
