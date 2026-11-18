Board: USB-C 5 V -> TPS54331 buck (U1) -> +3V3 rail, up to 3 A, on the
J4 output pins. Freshly assembled.

Works:
- No load: +3V3 reads 3.30 V.
- 200 mA load (resistor): 3.29 V, fine.

Fails:
- With the electronic load at 1.5 A and above, an ESP32 dev board also
  on +3V3 (about 400 mA peaks) resets at random.
- At 2.5 A the buck whines and +3V3 sags to about 3.0 V on the meter.

Measured (multimeter and a 100 MHz scope, 1.5 A load, phone-charger
5 V / 3 A supply):
- +5V at J1: 4.95 V.
- +5V at U1 pin 2 (VIN): 4.8 V on the meter; the scope shows about
  1.2 V peak-to-peak spikes at the switching frequency.
- +3V3 at J4: about 300 mV peak-to-peak ripple, with ringing on each
  switching edge.
- U1 gets warm (about 55 °C after a minute); the inductor stays cool.

Same result with a lab supply at 5.0 V instead of the charger.
