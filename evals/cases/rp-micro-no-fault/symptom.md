Board: RP2040 board (Pro Micro size). USB-C 5 V -> polyfuse -> Schottky
diode -> RAW -> RT9013-33 LDO -> +3V3. W25Q64 flash, 12 MHz crystal.
Freshly assembled, so the flash is blank.

Fails:
- Plugging into my laptop: nothing. No RPI-RP2 drive, no "device
  connected" sound, nothing new in Device Manager, not even an unknown
  device. Holding BOOT while plugging in, or BOOT + RESET, changes nothing.
- Tried both USB ports on the laptop.
- Cable: USB-C to USB-A, it charges my phone fine.

Measured (multimeter, on USB):
- RAW pin: 4.75 V.
- 3V3 pin: 3.30 V, steady.
- USB current meter: about 0.02 A.

Nothing gets warm. No visible solder bridges under a loupe.
