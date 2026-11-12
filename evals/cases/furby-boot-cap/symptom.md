Board: ESP32-S3-WROOM-1 toy robot board. USB-C or 1S Li-ion through a
power mux -> TPS63070 buck-boost -> +3V3. Motor driver, I2S microphone,
IMU and status LEDs.

Bring-up, firmware flashed over USB:

- After a cold power-on (USB plugged in, or battery switched on) the
  firmware doesn't run. The USB serial console shows the ROM banner
  ending in "waiting for download" (boot mode DOWNLOAD), every time.
- Pressing the RESET button once the board is powered starts the
  firmware normally. It then runs until the next power cycle.
