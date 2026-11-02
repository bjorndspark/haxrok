Board: STM32F030C8 board. 1S Li-ion via the power switch -> TPS63802
buck-boost -> +3.3V. USB mini-B feeds an MCP73831 charger and a CP2102 USB-UART
on USART1 (PA9/PA10). Programmed over SWD with an ST-Link.

Works:
- SWD flashing and debugging work.
- My test firmware blinks both LEDs, so it is running.
- The PC sees the CP2102 as a COM port (Silicon Labs driver) when USB is
  plugged in. The battery charges.

Fails:
- The firmware prints "hello" on USART1 at 115200 8N1 once a second and
  echoes back anything it receives. The terminal shows nothing at all, at
  115200 or any other baud rate. Typing into the terminal does nothing; the
  breakpoint in the RX interrupt never hits.
- Same with two different terminal programs and two PCs.

Measured (multimeter):
- +3.3V rail: 3.30 V. Battery: 3.85 V. VBUS: 5.0 V.
- PA9 sits at about 3.3 V.
- Battery current about 12 mA.

Nothing gets warm.
