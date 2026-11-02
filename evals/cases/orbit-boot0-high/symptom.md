Board: STM32F030C8 board. 1S Li-ion via the power switch -> TPS63802
buck-boost -> +3.3V. There is a BOOT0 slide switch and a RESET button.
Programmed over SWD with an ST-Link (STM32CubeIDE / STM32CubeProgrammer).

Works:
- ST-Link connects every time. Flashing reports success and "verify OK".
- Starting a debug session in CubeIDE: it stops at main(), I can step
  through the clock and GPIO init, and the LED turns on when I step over
  that line.

Fails:
- The firmware (blink both LEDs with HAL_Delay) never runs on its own: not
  after a power-cycle, not after pressing RESET. LEDs stay dark.
- Same in both positions of the BOOT0 switch.
- In the debugger, pressing Resume after the LED line: the LED never blinks.
  Pausing shows the PC at an address outside my program (0x1FFFxxxx).

Measured (multimeter):
- +3.3V rail: 3.30 V. Battery: 3.9 V.
- NRST: 3.3 V, drops to 0 V while RESET is pressed.
- Battery current: about 4 mA.

Nothing gets warm.
