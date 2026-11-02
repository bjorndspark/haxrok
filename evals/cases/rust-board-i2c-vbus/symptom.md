Board: ESP32-C3 dev board (ESP32-C3-MINI-1) with an SHTC3 temp/humidity
sensor and an ICM-42670 IMU on the I2C bus (GPIO8 = SCL, GPIO10 = SDA).
USB-C 5 V or a 1S LiPo -> SY8088 buck -> +3V3. MCP73831 charges the LiPo.

Works:
- On USB (with or without the LiPo): boots, flashes, Wi-Fi works, and both
  sensors read sensible values. An I2C scan finds 0x68 and 0x70.

Fails:
- On the LiPo alone (USB unplugged), the board boots and Wi-Fi works, but
  the I2C scan finds nothing, or sometimes random addresses. Sensor reads
  time out. Plugging USB back in fixes it straight away.

Measured (multimeter):
- 3V3 pin: 3.30 V on USB and on battery.
- LiPo: 3.9 V.
- SDA and SCL idle (no traffic), on USB: about 3.7 V.
- SDA and SCL idle, on battery: about 2.6-2.7 V.

Nothing gets warm.
