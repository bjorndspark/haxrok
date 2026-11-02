Board: RP2040 board (Pro Micro size). USB-C 5 V -> polyfuse -> Schottky
diode -> RAW -> RT9013-33 LDO -> +3V3. W25Q64 flash, 12 MHz crystal.
Freshly assembled, so the flash is blank.

Fails:
- Plugging into the PC: nothing. No RPI-RP2 drive, no "device connected"
  sound, nothing in Device Manager. Holding BOOT while plugging in or
  pressing RESET changes nothing.

Measured (multimeter, on USB):
- RAW pin: 4.7 V.
- 3V3 pin: 0.0 V (a few mV).
- USB current meter: 0.00 A.

Experiment:
- With USB plugged in, I fed 3.3 V from my bench supply (100 mA limit) into
  the 3V3 pin. The RPI-RP2 drive appeared straight away and the bench supply
  showed about 20 mA.
- Then I switched the bench supply off (leads still attached, reading 0 mA).
  The drive stayed, and 3V3 still read 3.3 V.
- Unplugging USB and plugging it back in: dead again, 3V3 at 0 V.

Nothing gets warm.
