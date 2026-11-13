Board: STM32F030C8 board. 1S Li-ion via the power switch -> TPS63802
buck-boost -> +3.3V. This revision gives VDDA its own RT9013-33 LDO
(U23) from the battery, for a quieter ADC reference. Firmware blinks
both LEDs with HAL_Delay and reads the battery on the ADC every second.

Works:
- ST-Link connects and flashes every time.
- Pressing RESET always starts the firmware: LEDs blink, ADC readings
  are steady.

Fails:
- After turning the power switch on, the firmware doesn't run about one
  time in five: the LEDs stay dark until I press RESET.
- Fails more often after the board has been off for only a second or
  two than after it has been off for a minute.
- The previous revision (VDDA on +3.3V with the other supply pins)
  started every time with the same firmware.

Measured (multimeter, running):
- +3.3V rail: 3.31 V. VDDA (U16 pin 9): 3.29 V. Battery: 3.9 V.
- NRST: 3.3 V.

Nothing gets warm.
