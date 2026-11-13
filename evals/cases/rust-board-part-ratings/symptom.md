Board: ESP32-C3 dev board (ESP32-C3-MINI-1). USB-C 5 V (or a 1S LiPo on the
battery pads) -> SY8088 buck (U5) -> +3V3 rail. MCP73831 charges the LiPo
from USB. A small batch, assembled and working; this is one board after a
few weeks of use.

Works:
- Everything at first: USB serial/JTAG enumerates, firmware flashes, the RGB
  LED and the sensors work, on USB and on battery.

Fails:
- From day one, on USB, a spot near the USB-C connector is too hot to keep
  a finger on after a minute or two. On battery alone it stays cool.
- Idle current on USB is higher than the firmware explains.
- After a few weeks one board stopped working on USB: plugging it in now
  trips the PC port / the USB meter shows a short. On battery it still
  runs.

Measured (on USB, no LiPo, idle firmware, a board that still works):
- 5V pin on the header: 4.7 V.
- 3V3 pin on the header: 3.31 V.
- USB current meter: about 0.09 A; the same firmware on a reference
  ESP32-C3 board idles at about 0.04 A.
