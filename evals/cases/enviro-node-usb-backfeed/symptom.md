Board: ESP32-WROOM-32E environmental sensor node. Li-ion battery
charged from a solar panel (LT3652) or USB-C (MCP73832), a 3.3 V LDO
behind a power switch, a CP2102N USB/UART bridge, a SARA-R5 LTE-M
modem, two INA219 current monitors, an I/O expander, a piezo buzzer and
terminal blocks for sensors.

The fix commit's message is the only source of an observation:

- With a USB cable plugged in, the board tries to run from USB power.
  The designer wants it to run from its batteries only.
