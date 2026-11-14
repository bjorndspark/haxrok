# Judged issues: enviro-node-usb-backfeed

Agent judgements from the 2026-11-14 sweep, confirmed by the user in
plan 012 step 5; `make eval-score` matches each review issue against
these (format in `evals/judged.py`).

## Solar buck freewheel diode D6 open
Verdict: wrong
Cites: d6
Why: `D6` (SS34) has its cathode on the LT3652 SW node and its anode on GND, the datasheet's catch diode. Nothing in it is open.

## ESP32 GPIO0 floating when idle
Verdict: wrong
Cites: gpio0
Why: GPIO0 has an internal pull-up in the ESP32, so it reads high at reset with `Q1` off and `SW1` open; boards like this rely on it.

## USB-C B-side data left open, a flipped cable has no data
Verdict: real
Cites: j1.b6|j1.b7|unconnected-(j1-dp2-padb6)|unconnected-(j1-dn2-padb7)
Why: Only DP1/DN1 (`J1.A6`/`A7`) reach the CP2102N; DP2/DN2 are open. A USB 2.0 plug has one data pair, so flipped it lands on B6/B7 and the port doesn't enumerate. Power still works flipped: the plug buses its own VBUS and GND pins, so the open VBUS_B/GND_B pads don't matter.

## SARA-R5 VCC behind the ferrite with no local capacitor
Verdict: real
Cites: fb1.2|vcc
Why: VCC (`U12.51`–`53`) connects only to `FB1`; all of `C23`–`C29` sit on the load switch's side. The SARA-R5 asks for capacitors at its VCC pins for transmit bursts.

## Power LEDs D3 and D10 reversed
Verdict: real
Cites: d3|d10
Why: Both LEDs have the anode on GND (`D3`) or on `R35` to GND (`D10`), and the cathode toward the solar or battery rail, so they're reverse-biased and never light.

## Solar and USB charger outputs paralleled on VBAT
Verdict: real
Cites: u5.9; u7.3
Why: The LT3652 BAT and the MCP73832 VBAT both sit on VBAT. With both sources present, each charger counts the other's current as battery current, so termination can come early. A may-fail, as raised.

## GPIO17 tied to +3.3V through R42 (0 Ω)
Verdict: real
Cites: r42
Why: `R42` (0) runs from +3.3V to GPIO17 with `R43` (10k) to GND, so the pin can't be driven low. The fix commit marks `R42`/`R43` not fitted (item 6) and moves GPIO17 back to the piezo (item 5).

## SARA-R5 PWR_ON has no pull-up
Verdict: wrong
Cites: u12.15|net-(q4-c)
Match: pull-up|pullup|float
Why: The SARA-R5's `PWR_ON` has an internal pull-up, and u-blox recommends driving it with an open-collector or open-drain switch to ground, which `Q4` is.
