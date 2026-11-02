Board: ESP32-C3 dev board (ESP32-C3-MINI-1). USB-C 5 V (or a 1S LiPo on the
battery pads) -> SY8088 buck (U5) -> +3V3 rail. MCP73831 charges the LiPo
from USB. Freshly assembled, first power-up.

Works:
- The charge LED comes on when a LiPo is attached and USB is plugged in,
  and goes out when the cell is full. The cell does charge.

Fails:
- Nothing else. The ESP32-C3 never shows up on the PC (no USB serial/JTAG
  device), the RGB LED stays dark, the BOOT and RESET buttons change nothing.
- Same on USB alone, LiPo alone, or both.

Measured (multimeter, on USB, no LiPo):
- 5V pin on the header: 4.7 V.
- 3V3 pin on the header: 0.73 V, steady.
- USB current meter: about 0.01 A.

Nothing gets warm. No visible solder bridges under a loupe.
