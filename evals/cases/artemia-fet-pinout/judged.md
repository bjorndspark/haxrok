# Judged issues: artemia-fet-pinout

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`).

## J25 battery connector shorted to itself
Verdict: wrong (draft)
Cites: j25.2
Why: `J25` and `J26` are the two terminals of the fuel cell input, each a 2-pin header with both pins on one net: `J25` on +BATT, `J26` on GND. No cell's plus and minus land on `J25`.

## +BATT to ADP5091 VIN overvoltage if it's a Li-ion cell
Verdict: wrong (draft)
Cites: +batt
Match: overvolt|4.2|li-ion|lithium|absolute
Why: +BATT is the harvester input on the sMFC_ADP sheet: a sediment microbial fuel cell on `J25`/`J26`, well under a volt, not a battery. The Li-ion cell the review assumes isn't on the board.

## AM1815 has no bypass on either supply
Verdict: real (draft)
Cites: u6.13|net-(u6-vbat)
Match: bypass|decoupl|capacitor
Why: `U6` VCC sits on `D1`'s cathode with no capacitor, and VBAT is fed through `R26` with none at the pin. The AM1815 datasheet places a bypass capacitor on VCC.

## TPS22860 outputs have no capacitor, V_LORA feeds the radio
Verdict: real (draft)
Cites: /application/v_{lora}
Match: bypass|capacitor|caps
Why: V_LORA (`U13` VOUT) goes to the RFM95W `U14` and `J10` with no capacitor, so transmit bursts see the switch's on-resistance. A may-fail, as raised. A review that also asks for a storage capacitor on `U1` BAT is wrong on that point: `J27` ties BAT to V_ADP, which has `C7`, `C8` and `C13`–`C15`.

## SWD reset AC-coupled through C30
Verdict: real (draft)
Cites: c30
Why: The SWD header's nRESET (`J24.10`) reaches the Artemis nRESET (`U12.50`) only through `C30` (470n), the FT230X auto-reset capacitor. A probe that holds reset low sees only an edge.

## V_GOOD held low by R19, boost never enabled
Verdict: real (draft)
Cites: r19|/power management/v_{good}
Why: The second fault the fix commit closes (issue #6): `R19` (10k to GND) against `R17` (1M up) holds the TLV803E's open-drain output near 1 % of V_ADP, so `U5` D always reads low.

## EN_PULSE biased mid-rail by R18 and R27
Verdict: real (draft)
Cites: r18|/power management/en_{pulse}
Why: Issue #6 again: `R18` (10k to GND) against `R27` (10k up) puts the RTC's open-drain FOUT/~IRQ at half of V_ADP when idle, an invalid level for `U5`'s clock.
