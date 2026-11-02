Root cause: none in the schematic. This is the unmodified board.

The symptom was written for an off-schematic cause: a charge-only USB cable
(no D+/D- wires). It powers the board, so the rails and the ~20 mA are
normal, but the host never sees a device. "It charges my phone" is true of
charge-only cables too.

The USB path in the schematic is correct: J1 D+/D- (both rows) -> 27 R
series resistors R1/R2 -> RP2040 USB_DP/USB_DM, 5.1k CC pull-downs R6/R7,
+3V3 to USB_VDD, 12 MHz crystal with 1k R3 on XOUT, QSPI flash with
BOOT/QSPI_SS via R4/R5. With a blank flash the RP2040 falls back to the
USB bootloader, so it should enumerate with no button press.

Good off-schematic checks: try a cable known to carry data (one a phone
syncs files over); continuity of the cable's D+/D-; solder on the USB-C
D+/D- pins (A6/B6/A7/B7), R1/R2 and the crystal; then the RP2040 itself.

Source: https://github.com/siderakb/rp-micro at a7baf36,
`hardware/rp-micro.kicad_sch`, unmodified. License: CERN-OHL-P-2.0.

Scoring: PASS if the answer reports no schematic design error: it says
the schematic looks correct for this symptom and ranks only off-schematic
causes (cable, solder, part). MISS if any hypothesis, at any rank, calls a
correct part of the design an error or recommends reworking it. Ranking
it low or marking it INFERRED does not save it: the user would still cut
or replace a correct part.

Auto-check: none
