# Judged issues: usbc-buck-no-fault

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## CC REF stuck at 5 V, buck never enables
Verdict: wrong (draft)
Cites: cc ref|/1.2v|/0.65v
Match: reference|cc ref|threshold
Why: `J2` and `J3` are one 2x3 jumper block. A jumper across them ties CC REF to a tap of the R4/R5/R6 divider, which `R10` then feeds from +5V: about 1.22 V or 0.64 V. The PCB's pad nets match the schematic.

## J5 "5V conn." carries BUCK-EN, not +5V
Verdict: wrong (draft)
Cites: j5
Why: BUCK-EN is the drain of the P-FET `Q2` (AO3401A), the +5V switched on once the source advertises enough current. It is the board's 5 V output, and it also drives `U1` EN (rated to 6 V). A load on `J5` draws through `Q2`, not through a weak enable line.

## CC1/CC2 into the LM358 with no ESD protection
Verdict: real (draft)
Cites: cc1|cc2|j1.a5|j1.b5
Match: esd|static
Why: `CC1` and `CC2` run from the USB-C socket `J1` to the LM358 inputs with only the 5.1k Rd to GND. No protection part is on them.

## USB-C shield left floating
Verdict: real (draft)
Cites: j1.s1|unconnected-(j1-shield-pads1)
Match: shield|shell
Why: The board works with the shell open, but tying it to GND (directly or through an RC) gives ESD and cable noise a path. Fair as an improvement, not as an error.
