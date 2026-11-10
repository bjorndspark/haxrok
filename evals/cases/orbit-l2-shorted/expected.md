Root cause (planted fault):

The TPS63802 (U15) L2 pin (U15.7) is on its own output, +3.3V, together
with the inductor's L2 end (L1.2), instead of on a net of its own. L2 is
the boost-side switch node: in boost cycles its low-side switch pulls it
to GND, which here shorts +3.3V (and the output capacitors) to ground
through U15. The inductor still runs from L1 (U15.9) to the output, so
the converter can work as a buck and the rail settles at its set
voltage; the fault shows only while it runs boost cycles, early in soft
start when the output is low, as large current pulses out of +3.3V into
U15.7. A weak or current-limited source sags under them, so start-up
needs far more input current than the load.

Fix: give L2 its own net again: L1.2 and U15.7 joined to each other and
to nothing else. On the bench, cut the trace from the L1/L2 node to
+3.3V.

Planted edit (`ORBIT.kicad_sch`): added a `power:+3.3V` symbol (#PWR060)
at the inductor's L2 end (236.22, 60.96 mm), so the net `Net-(U15-L2)`
(L1.2, U15.7) joins +3.3V. ERC before and after, through the backend
container's kicad-cli (KiCad 9.0.2): 215 -> 216 messages; the only new
one is "the current configuration does not include the symbol library
'power'" for #PWR060. `erc.txt` is that container's report, so it also
carries library-configuration warnings the older orbit cases (exported
with a configured KiCad) don't have. `netlist.txt` was exported by KiCad
10.0.8 with the `_<pin>` suffix it adds to pin functions stripped; the
container's KiCad 9 export of the same file gives the same text, and the
unmodified board gives `orbit-uart-swapped`'s netlist except its two
USART nets.

Symptoms: the start-up current (needs 0.8-1.2 A from a bench supply) is
from a private board with the same fault on a TPS63070 (6 V there,
4.0 V here, as orbit is 1S). That the board keeps running at 0.5 A once
started and settles at 3.30 V follows from a hand-built ngspice study of
that board, not from hardware.

Source: https://github.com/mohamedboubaker/Battery-Powered-STM32-Board-With-USB
at a33a81c, `Hardware/ORBIT.kicad_sch`. License: MIT.

Scoring: PASS if U15's L2 pin (U15.7) wired to its own output (+3.3V) is
in the top 3 hypotheses. Review mode: PASS if it's listed as "won't work
as drawn" or "may fail".

Auto-check: l2|u15.7; u15|tps63802|buck-boost|converter|regulator; +3.3v|3.3v|vout|output
