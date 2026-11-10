Board: STM32F030C8 board. 1S Li-ion via the power switch -> TPS63802
buck-boost -> +3.3V. Firmware blinks both LEDs with HAL_Delay.

Bring-up from a lab supply on the battery connector (BAT1) instead of a
cell, set to 4.0 V, power switch on:

- With the supply's current limit at 0.5 A, the board doesn't start: the
  LEDs stay dark and the supply shows current limit at turn-on.
- With the limit raised to 0.8 A it sometimes starts; at 1.2 A it starts
  every time.
- Once it has started, it keeps running: the LEDs blink, and turning the
  limit back down to 0.5 A doesn't stop it.

Measured (multimeter, running):
- +3.3V rail: 3.30 V.
