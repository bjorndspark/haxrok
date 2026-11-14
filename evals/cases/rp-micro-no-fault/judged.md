# Judged issues: rp-micro-no-fault

Agent drafts from the 2026-11-14 sweep; `make eval-score` matches each
review issue against these (format in `evals/judged.py`). The three
rp-micro cases share one board, so their entries are the same.

## SB1/SB2 short GPIO10/11 to ground
Verdict: wrong (draft)
Cites: sb1|sb2
Match: gnd|ground
Why: SB1 and SB2 are three-pad solder jumpers. The default bridge doesn't tie the GPIO to ground (plan 012 step 1).

## USB D+/D- with no ESD array
Verdict: real (draft)
Cites: j1|u1.46|u1.47|/rpd+|/rpd-|usb_d+|usb_d-
Match: esd
Why: `USB_D+` and `USB_D-` run from the USB-C socket `J1` through 27R (`R1`, `R2`) to the RP2040. No protection part is on them.

## Crystal load caps 20pF vs 15pF
Verdict: wrong (draft)
Cites: y1|c3|c4
Match: 20pf|20 pf|20p
Why: The schematic doesn't name Y1's part, so its CL is unknown. The reference design's 15pF caps belong to the ABM8-272-T3 (CL=10pF), and 20pF is right for a crystal with CL around 12-14pF.
