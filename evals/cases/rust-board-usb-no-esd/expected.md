Root cause (planted fault): the USB D+/D- lines have no ESD protection.
The original board puts a TVS diode (D8 on D+, D5 on D-, LESD5D5.0CT1G)
from each line to GND next to the USB-C connector; here both are gone, so
a discharge at the connector (a person charged by a carpet touching the
plug) goes straight into the ESP32-C3's GPIO19/USB_D+ pin. Its on-chip
protection is rated for handling (HBM), not IEC 61000-4-2 contact
discharge, and the pin was damaged: the low diode-mode reading on D+ and
the dead USB while the rest of the chip runs. Fix: replace the module (or
the chip), and put the TVS diodes (or an ESD array such as USBLC6-2) back
on D+/D- next to J1.

Planted edit (from the `rust-board-part-ratings` netlist): C15 and R15
restored to the original "4.7uF 16V" and "100kR 1%"; D5 and D8 removed
from the component list and from the USB_D-, USB_D+ and GND nets. The ERC
is the original board's (70 messages), copied unchanged: it names neither
diode. The connector-ESD check (`esd.py`) lists J1's D+/D- as may fail;
on the unmodified board it lists nothing for J1.

Source: https://github.com/esp-rs/esp-rust-board at efe1e8a,
`hardware/esp-rust-board/esp-rust-board.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if the top hypothesis is ESD damage to the C3's USB pin(s)
through the unprotected D+/D- lines (no TVS/ESD part between J1 and U3).
Naming the damaged pin alone, without the missing protection, is PARTIAL.

Auto-check: esd|tvs|electrostatic; d+|d-|usb
