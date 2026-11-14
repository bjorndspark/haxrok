# Judged issues: orbit-l2-shorted

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`). The four orbit
cases share one board, so their entries are the same.

## L1 fitted as 2.2uH
Verdict: wrong (draft)
Cites: l1
Match: 2.2
Why: L1's value is `0.47uH`, the TPS63802's inductor. `DFE201612E-2R2M` is the footprint's library name, not a fitted part number. The 0.47uH part (`DFE201612E-R47M`) shares that package.

## PWR switch cuts the charger off the battery
Verdict: real (draft)
Cites: pwr1
Match: charg
Why: `PWR1` sits between `BAT1.2` and `+BATT`, and the MCP73831's VBAT (`U1.3`) is on `+BATT`. With the switch off, USB can't charge the battery. With it on, the load shares the charger's output and can upset its charge finish.

## PWR switch common may be pad 2
Verdict: real (draft)
Cites: pwr1
Why: `PWR1` is an SPDT slide switch (`K3-1270L-F1`) wired by pads 1 and 3, with pad 2 open. On most SPDT slide switches pad 2 is the common, which would leave the battery never connected. The switch's drawing hasn't been checked yet.

## USB D+/D- with no ESD array
Verdict: real (draft)
Cites: usb1|/usb_d+|/usb_d-|usb_d+|usb_d-
Match: esd
Why: `/USB_D+` and `/USB_D-` run from the mini-USB socket `USB1` straight to the CP2102 (`U22`). No protection part is on them.

## HSE load caps 30pF over 20pF
Verdict: wrong (draft)
Cites: c11|c12
Match: load|30p
Why: The STM32F030 datasheet gives 5-20pF as a typical range, not a limit. The right value depends on the crystal's CL, which the schematic doesn't give, and 30pF suits a common 8MHz crystal with CL=20pF.

## MCU and CP2102 back-power each other's UART
Verdict: real (draft)
Cites: u22
Match: is off|unpowered|back-power|back power|partly power
Why: The CP2102 runs from `VBUS` and the MCU from `+3.3V` off the battery. `USART1_TX` and `USART1_RX` link them directly, so whichever side is on drives the other's pins while that side is off.
