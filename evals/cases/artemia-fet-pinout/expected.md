Root cause (real bug, fixed upstream in a05f688, issue #7 "Q1 and Q2
footprint pins are wrong"):

Q1 and Q2 are BSS123 N-MOSFETs (the Datasheet field, `parts.txt`) drawn
with the generic `Device:Q_NMOS_DGS` symbol, whose pins are 1 = D,
2 = G, 3 = S. The BSS123 in SOT-23 is 1 = G, 2 = S, 3 = D. On the built
board each FET's gate sits on the flip-flop input its drain should pull
low, its source on the MCU's 1k gate resistor, and its drain on GND:

- Q1: pad 1 on `~PRE` of U5 (SN74AUP1G74) with R20 (160k) to V_ADP,
  pad 2 on R22 (1k) from FORCE_ON (U12 GPIO43), pad 3 on GND.
- Q2: pad 1 on `~CLR` of U5 with R21 (160k) to V_ADP, pad 2 on R23
  (1k) from SHD (U12 GPIO42), pad 3 on GND.

So the MCU can neither set nor clear U5, whose Q enables the 3.3 V
boost (U7 EN), and R20 / R21 hold each gate high, so the channel ties
GPIO43 and GPIO42 to GND through 1k. The issue's own words: "Source and
Gate are flipped".

The same fix commit closes issue #6 ("Pulldown resistors on open-drain
lines are causing issues, R18 and R19"): R18 (10k) pulls EN_PULSE (the
RTC's open-drain FOUT/~IRQ, U6.11, with R27 10k up) and R19 (10k) pulls
V_GOOD (the TLV803E's open-drain ~RESET, U2.2, with R17 1M up) to GND.
The issue: "The open drain pull up resistors with these ones effectively
form unwanted voltage dividers". V_GOOD (U5's D input) sits near 1 % of
V_ADP and EN_PULSE (U5's clock) near half of it. A review that raises
this is right; it is judged real in `judged.md`.

Fix (a05f688): a `Transistor_FET:BSS123` symbol for Q1 and Q2 (G on
R22 / R23, S on GND, D on `~PRE` / `~CLR`), and R18 and R19 removed.

The repo's issues give no bench observation beyond the causes, so the
case has no `symptom.md` and is scored in review mode.

Source: https://github.com/patlab-ucsd/artemia_hardware at a19e227 (the
parent of the fix a05f688), `artemia.kicad_sch`, unmodified. License:
Solderpad Hardware License v2.1 (a wraparound of Apache-2.0).

Scoring: in review mode, PASS if Q1/Q2's pinout (the DGS symbol on a
BSS123) is listed, as will fail or may fail.

Auto-check: q1|q2|bss123; pinout|pin-out|pin order|pin mapping|pin assignment|dgs|sot-23|flipped|swapped|gate and source|source and gate
