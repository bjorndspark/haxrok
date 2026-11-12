Root cause (real bug, confirmed on the built board):

GPIO0 (U1.27, the boot-mode strapping pin) has C5 (100 nF) to GND, next to
the BOOT button SW2, and no external pull-up. The only thing that pulls it
high is the module's internal weak pull-up (typ 45 kΩ, datasheet p.27).

EN (U1.3) is held low at power-up by the TPS3839 supervisor U4 through R1
(10k) with C1 (10 µF) + C2 on EN, so EN rises long after +3V3. That
doesn't help: while EN is low the chip is powered down and its pull-up is
off, so C5 stays empty. When EN rises the pull-up starts charging C5 from
0 V with τ ≈ 45 kΩ × 100 nF = 4.5 ms, while the chip latches the strapping
pins within the hold time after EN high (tH min 3 ms, datasheet p.14).
GPIO0 reads low and the chip enters download mode on every cold boot.

Pressing RESET (SW1 on EN) works: C5 is already charged from the previous
run and nothing discharges it while EN is low, so GPIO0 reads high.

Fix: a 10 kΩ pull-up from GPIO0 to +3V3 (τ ≈ 1 ms), and/or shrink C5 to
1 nF or remove it. On the bench, a 10k from the C5 pad to a +3V3 pad.

Found by the app's pre-fab review (run 20261112-045722-2defa1, #1, "will
fail") before the cause was known on the bench. The netlist, ERC and
datasheet URLs come from that run's record (data/runs/), not a fresh
kicad-cli export; the ERC is the app's rendering of KiCad's report.

Source: the maintainer's own board (furby_esp32_s3), not open source.

Scoring: PASS if GPIO0/BOOT held low at reset by C5 (no external pull-up)
is in the top 3 hypotheses. In review mode, PASS if it's listed at all,
as will fail or may fail.

Auto-check: gpio0|boot|u1.27; c5|capacitor|100n; pull-up|pullup|download|low
