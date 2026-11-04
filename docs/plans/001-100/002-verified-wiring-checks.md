# Plan 002: More verified checks before the deadline

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-04 | 2026-11-04 | In progress | — | `001-mvp-scope.md`, `evals/README.md` |

The model already ranks three planted faults #1 (`orbit-uart-swapped`,
`orbit-boot0-high`, `rp-micro-ldo-en`), but only as "Reasoned: check it".
On BOOT0 and EN it has also made up part facts (an `nBOOT_SEL` option
byte, EN thresholds that changed between runs). A backend check turns each
of these into a verified issue and gives the model facts to cite instead of
guessing. Each item below is a flagged `Calculation` in `calc.py`, using the
same pattern as `usb_cc` and `decoupling`.

The order is by value. Each item ships as its own commit with green CI, so
any commit can be the submission.

## Rules for every item

- Part facts (pin functions, thresholds) come from datasheets fetched now,
  never from memory. The source is named in the rule, as `DROPOUT_RULES`
  already does.
- When the check isn't sure, say nothing, or give an unflagged hint. A false
  alarm on a correct board costs more than a missed fault.
- Run `calc.lookup` on every eval case offline (`parse_compact`, no
  gateway). Only the planted case may change. Then do 1–3 gateway runs:
  the planted case in review mode and `rp-micro-no-fault`.
- Unit tests in `backend/tests/test_debug.py`; then `make lint test`.
- Update the `calc.py` module docstring, the `Calculation.kind` literal and
  docstring, and the "(none: …)" line in `format_calculations`.

## 1. UART lines wired TX-to-TX (`uart`): done

A net that joins two transmit outputs, or two receive inputs and no
transmit output.

- A pin's direction comes from its name: a whole token `TX`, `TXD`, `U0TXD`,
  `TXD0`, `USARTn_TX` and the RX forms. MCU pins named only by port
  (`PA9`, `GPIO0`) get their direction from a small table read from the
  datasheet: STM32F0 PA9/PA10 = USART1_TX/RX (AF1), PA2/PA3 = USART2;
  RP2040 GPIO0/GPIO1 = UART0 TX/RX (function F2). Net names are never used
  for direction, because a net is named from one end's point of view.
- Only pins on ICs (`U`, `IC`) count. Connectors are skipped: whether a
  header pin says "TX" for the board or for the cable is a convention.
- Skip a pin pair that shares a non-direction token (`RXD/GPIO20` and
  `GPIO20/U0RXD`). That's a pass-through symbol carrying the same signal,
  like `rust-board`'s U4, not two ends of a link.
- Flagged text: what both pins do, why nothing gets through, the fix
  (cross the lines), and the STM32 USART `SWAP` bit when the MCU is an
  STM32F0.
- Planted: `orbit-uart-swapped` flagged. Must stay quiet: `orbit-boot0-high`
  (the same board wired correctly), `rust-board-*`, `rp-micro-*`.

## 2. Enable and boot pins that can't reach their working level (`enable_pin`): EN done, BOOT0 blocked

Two cases. Both are wiring facts, so no thresholds are needed:

- **A regulator's EN on its own output.** For a part with an `EN` and a
  `VOUT`/`OUT` pin on the same net: the output is 0 V until EN is high, so
  it never starts. Planted: `rp-micro-ldo-en`. Must stay quiet: EN tied to
  VIN (`rp-micro-no-fault`'s RT9013 on RAW, ORBIT's TPS63802 on +BATT).
- **STM32 BOOT0 that can't be low.** Walk out from the `BOOT0` net
  through resistors and switch throws, and collect the rails it can
  reach. Flag it when no path leads to ground. A switch counts as joining
  its common pin to each throw, from the symbol's pins. RM0360 (the
  STM32F030 reference manual) gives the boot mode table. Planted:
  `orbit-boot0-high`. Must stay quiet: `orbit-uart-swapped`, whose BOOT0
  switch selects +3.3V or R2 to GND.
- BOOT0 is blocked: st.com doesn't serve RM0360 or the datasheet to this
  machine, and the boot mode table isn't in ST's GitHub data. It waits for
  a copy of the document.
- A floating EN (nothing on the net but the IC pin) is left out: whether
  that's a fault depends on each part's internal pull-up.

## 3. Pull-up to a rail above the IC's supply (`overvoltage_pullup`): done

The I²C check already flags pull-ups to VBUS. This applies the same rule
to any IC signal pin: a resistor from the pin's net to a named rail more
than 0.3 V above the IC's lowest named supply (`_supply()`).

- An IC's supply is its I/O supply: a core-only rail (the RP2040's 1.1 V
  DVDD) is skipped. That also fixed the I²C check, which flagged ordinary
  3.3 V pull-ups on any RP2040.
- Skip ground and named rails, the IC's own power, enable and sense
  pins (an LDO's EN pulled to VIN is normal), I²C nets (already covered),
  nets with a resistor to ground (a divider), and ICs with no named supply.
- On STM32 parts it's an unflagged hint: many of their pins are 5 V
  tolerant ("FT" in the pin table), and the netlist can't say which.
- No planted case yet. Only a hand-built unit test, plus the offline sweep
  showing no false alarms on any eval board.

## 4. Simulate the EN/RESET RC delay (`spice.py`)

Draw each `rc_delay` the way the flagged I²C line is drawn: the pin
voltage rising through R×C from 0 V, with the 63% point marked. This finds
nothing new. It's demo polish.

- Make `Simulation` general: time in seconds with a display unit, a
  reference line instead of `v_supply`, and `rise` optional. The I²C plot
  keeps its current look.
- Only for `rc_delay`s whose pull-up rail has a named voltage. Any ngspice
  failure leaves the card without a plot, as now.

## Out of scope for now

- The agent loop (stretch 2): the gateway's ~90 s silence and its 503s
  under parallel load make several round trips too slow before the close.
- Part substitutes (stretch 4): there's no parts API.
