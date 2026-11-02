Root cause (planted fault):

The I2C pull-ups R17 (SCL, IO8_SCL) and R18 (SDA, IO10_SDA) go to VBUS
instead of +3V3.

On USB, VBUS is ~4.7 V, so the bus idles above the 3.3 V rail (clamped
through the ESD diodes of the ESP32-C3 and the sensors). It works, but it
over-drives every pin on the bus. On battery, VBUS is unpowered and only
R15 (100k) holds it to GND. The pull-ups then go nowhere useful: idle level
comes only from the ESP32's weak internal pull-ups, and SCL and SDA are
coupled to each other through R17 + R18 (20k). When a device pulls SDA low,
SCL is dragged to ~1 V, so every transfer is corrupted.

Fix: return R17 and R18 to +3V3. On the bench, lift the top pads of R17/R18
and wire them to a 3V3 point.

Planted edit (`esp-rust-board.kicad_sch`): the two power symbols above R17
and R18 (#PWR0134, #PWR0135) changed from `power:+3V3` to `power:VBUS`
(lib_id, Value, and `symbol_instances` value). ERC before and after: same
70 messages; only the two "symbol modified in library" warnings for those
symbols now say VBUS instead of +3V3.

Source: https://github.com/esp-rs/esp-rust-board at efe1e8a,
`hardware/esp-rust-board/esp-rust-board.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if the I2C pull-ups on VBUS instead of +3V3 are in the top 3
hypotheses.

Auto-check: r17|r18|pull-up|pullup; vbus
