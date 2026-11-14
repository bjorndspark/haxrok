# Judged issues: artemia-fet-pinout

Agent judgements from the 2026-11-14 sweep, confirmed by the user in
plan 012 step 5; `make eval-score` matches each review issue against
these (format in `evals/judged.py`).

## J25 battery connector shorted to itself
Verdict: wrong
Cites: j25.2
Why: `J25` and `J26` are the two terminals of the fuel cell input, each a 2-pin header with both pins on one net: `J25` on +BATT, `J26` on GND. No cell's plus and minus land on `J25`.

## +BATT to ADP5091 VIN overvoltage if it's a Li-ion cell
Verdict: wrong
Cites: +batt
Match: overvolt|4.2|li-ion|lithium|absolute
Why: +BATT is the harvester input on the sMFC_ADP sheet: a sediment microbial fuel cell on `J25`/`J26`, well under a volt, not a battery. The Li-ion cell the review assumes isn't on the board.

## AM1815 has no bypass on either supply
Verdict: real
Cites: u6.13|net-(u6-vbat)
Match: bypass|decoupl|capacitor
Why: `U6` VCC sits on `D1`'s cathode with no capacitor, and VBAT is fed through `R26` with none at the pin. The AM1815 datasheet places a bypass capacitor on VCC.

## TPS22860 outputs have no capacitor, V_LORA feeds the radio
Verdict: real
Cites: /application/v_{lora}
Match: bypass|capacitor|caps
Why: V_LORA (`U13` VOUT) goes to the RFM95W `U14` and `J10` with no capacitor, so transmit bursts see the switch's on-resistance. A may-fail, as raised. A review that also asks for a storage capacitor on `U1` BAT is wrong on that point: `J27` ties BAT to V_ADP, which has `C7`, `C8` and `C13`–`C15`.

## SWD reset AC-coupled through C30
Verdict: real
Cites: c30
Why: The SWD header's nRESET (`J24.10`) reaches the Artemis nRESET (`U12.50`) only through `C30` (470n), the FT230X auto-reset capacitor. A probe that holds reset low sees only an edge.

## V_GOOD held low by R19, boost never enabled
Verdict: real
Cites: r19|/power management/v_{good}
Why: The second fault the fix commit closes (issue #6): `R19` (10k to GND) against `R17` (1M up) holds the TLV803E's open-drain output near 1 % of V_ADP, so `U5` D always reads low.

## EN_PULSE biased mid-rail by R18 and R27
Verdict: real
Cites: r18|/power management/en_{pulse}
Why: Issue #6 again: `R18` (10k to GND) against `R27` (10k up) puts the RTC's open-drain FOUT/~IRQ at half of V_ADP when idle, an invalid level for `U5`'s clock.

## ADP5091 BAT has no storage capacitor
Verdict: real (draft)
Cites: u1.17|/smfc_adp/bat_{out}
Match: storage|capacitor
Why: `U1.17` (BAT) goes only to `J27.1`, a 3-pin header whose pin 2 is `V_ADP` and pin 3 is `SYS`; nothing on the board stores charge on BAT. The reviews quote the ADP5091 datasheet as needing at least 100 µF there; that hasn't been checked against the datasheet yet.

## REG_D0/D1 mode pins float with the jumpers open
Verdict: wrong
Cites: u1.23|u1.24|j1.2|j2.2
Match: float|pull|undefined
Why: `J1` and `J2` are 3-pin headers between `SYS` (pin 1) and `GND` (pin 3), with the mode pin on pin 2. A shunt sets each mode by design; the pins are open only on a board fitted without its shunts.

## USB D+/D- with no ESD array
Verdict: real
Cites: u10|j16|/application/artemia_artemis/usbdp|/application/artemia_artemis/usbdm
Match: esd
Why: `J16`'s D+ and D- reach the FT230X (`U10`) through `R36`/`R37` only, with no ESD array; the same gap is a planted bug in `rust-board-usb-no-esd`.
