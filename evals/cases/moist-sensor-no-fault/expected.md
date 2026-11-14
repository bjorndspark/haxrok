Root cause: none in the schematic. This is the unmodified board, a
review-only case: it has no `symptom.md`, and `make eval-score` reviews
it for false alarms.

The board: a capacitive soil-moisture and temperature sensor on RS485
(Modbus), sold on Tindie. Supply VCC from the 4-pin cable (P2) ->
P-FET Q1 for reverse polarity (gate on GND, drain on VCC, source to the
regulator) -> MCP1702 LDO (U2, 1uF in and out) -> +3V3. An NUP4004 TVS
array (D4) clamps VCC and the bus lines A/B. ATtiny441 (IC1) with a
16 MHz crystal and 13pF caps; PB2 excites the probe track through 330R,
and two Schottkys (D1, D2) with 1nF and 1M dividers rectify it into
PA5/PA7. NTC thermistor (TH1) against a 0.1% 10k (R2) on PA3. SN65HVD72
transceiver (U1): RO/DI on the UART pins, ~RE pulled up (R3) and DE
pulled down (R9), so it listens and doesn't drive until the firmware
asks. ISP header P1 shares the SPI pins.

What a reviewer might wrongly flag:
- Q1 "backwards": a P-FET reverse-polarity switch has its drain on the
  input and source on the load.
- No 120R termination on A/B: termination belongs at the two ends of
  the bus, not on every sensor.
- No fail-safe bias resistors on A/B: the SN65HVD72 has fail-safe
  receivers built in.
- The SP3481EN symbol for an SN65HVD72: same pinout, rescued library.
- PA5/PA6/PA4 also used by the ISP header: shared on purpose for
  programming. R9 holds DE low, so the driver toggles only while
  programming on a live bus.
- U2's value `MCP1702` with no voltage suffix: the rescued symbol's
  name; the rail is `+3V3`.

Worth checking when judging: the ATtiny441 is rated to 16 MHz only from
4.5 V, and it runs at 3.3 V here, unless the firmware sets the clock
prescaler.

Source: https://github.com/Miceuz/rs485-moist-sensor at 8cba0cd,
`rs485-moist-sensor.kicad_sch`, unmodified. License: Apache-2.0.

Scoring: PASS if the review calls no correct part of the design an
error. Each issue it raises is judged in `judged.md`.

Auto-check: none
