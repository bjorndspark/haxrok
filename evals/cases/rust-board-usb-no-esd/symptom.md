Board: ESP32-C3 dev board (ESP32-C3-MINI-1). USB-C 5 V (or a 1S LiPo on the
battery pads) -> SY8088 buck (U5) -> +3V3 rail. MCP73831 charges the LiPo
from USB. Firmware is flashed over the C3's built-in USB serial/JTAG. A
small batch, assembled and working; this is one board after a few months
on a desk.

Works:
- Everything at first: USB serial/JTAG enumerates, firmware flashes, the RGB
  LED and the sensors work, on USB and on battery.
- Still now: on USB or battery the firmware runs (the RGB LED blinks, the
  sensors log over Wi-Fi), the LiPo charges, the 3V3 rail is fine.

Fails:
- One dry winter morning the board stopped showing up on the PC: no serial
  port, no JTAG, "USB device not recognized" on some ports, nothing at all
  on others. It had been plugged in and out many times that week, by
  someone walking across a carpet. Other cables and PCs: the same. Other
  boards from the batch on the same cable: fine.

Measured (unpowered, multimeter in diode mode, red lead on GND):
- USB_D- pad to GND: 0.62 V, the same as on a working board.
- USB_D+ pad to GND: 0.18 V; a working board reads 0.61 V.
- 3V3 and 5V pins: 3.31 V and 4.95 V on USB.
