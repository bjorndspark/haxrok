Root cause (real bug, fixed upstream in 1a1253b, "Fixed most errors on
the 1.0.1 board revision", item 2: "Changed the USB/UART bridge power
supply wiring so it will not try to power the board when a USB cable is
plugged in"):

U1 (CP2102N) is wired bus-powered: VREGIN (U1.7) on USB VBUS, so its
internal regulator runs from USB, and VDD (U1.6), that regulator's
output, is on the board's +3.3V rail. With a USB cable in, the CP2102N
drives the whole +3.3V rail (ESP32 U2, I/O expander U3, the level
shifter's B side, the piezo): with the power switch off (J10, between
U10's output and +3.3V) the board tries to run from the CP2102N's
regulator, and with it on, that regulator is in parallel with U10
(XC6215).

Fix (1a1253b): U1 self-powered, VREGIN and VDD both on +3.3V, and C1
(4.7uF) / C2 (0.1uF) on VBUS removed. The board then needs its
batteries ("Batteries must be present").

The same commit fixes more, each a real flaw a review may also raise
(judged real in `judged.md`):

- Item 3: the battery monitor U8 (INA219) has VS, A0 and A1 on VBAT;
  the fix puts them on +3.3V "so it can be read while the battery is
  being charged via the solar panel".
- Item 4: the 3.3 V regulator U10 (CE tied to VIN) runs from VBAT ahead
  of the power switch J10, "so it is not running all the time" after
  the fix moves it behind the switch.
- Item 5: GPIO17 and GPIO27 swapped between the piezo driver (R11, Q2)
  and the rain gauge input (J6.9/J6.10, R42, R43), reverted to the
  firmware's assignment.

Items 1, 6, 7 and 8 (a UART TX jumper, R42/R43 marked not fitted, the
MCP73832 part number, ERC power flags) are not faults a netlist shows.

Source: https://github.com/DPIclimate/enviro-node at e0eeff4 (the
parent of the fix 1a1253b), `electronics/motherboard/enviro-node/enviro-node.kicad_sch`,
unmodified. License: MIT.

Scoring: PASS if U1's VDD (its regulator output) on +3.3V while VREGIN
is on VBUS is in the top 3 hypotheses. In review mode, PASS if it's
listed at all, as will fail or may fail.

Auto-check: u1|cp2102; vdd|vregin|regulator; vbus|usb|back-feed|backfeed|back-power|backpower|parallel|contention
