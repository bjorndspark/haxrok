Board: RP2040 board (Pro Micro size). USB-C 5 V -> polyfuse -> Schottky
diode -> RAW -> RT9013-33 LDO -> +3V3. W25Q64 flash, 12 MHz crystal.
Programmed with a blink sketch; the LED blinks when it has power.

Works:
- USB-A to USB-C cable from my laptop's USB-A port: LED blinks, the board
  shows up as a serial port.
- A cheap USB-C to USB-C cable from a phone charger: also works.

Fails:
- My laptop's own USB-C charger with the cable that came with it (100 W,
  USB-C to USB-C): nothing. LED off.
- Same cable into the laptop's USB-C port: also nothing, not detected.

Measured (multimeter, on the laptop charger's cable):
- VBUS at the connector: 0.0 V.
- RAW pin: 0.0 V.

Swapping the plug's orientation makes no difference. Nothing gets warm.
