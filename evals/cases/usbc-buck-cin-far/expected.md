Root cause: U1's input capacitors are far from its VIN pin. The planted
edit moves C1, C2 and C3 (the +5V input caps of the TPS54331 buck U1)
from beside U1 to beside the USB-C connector J1 and reroutes them, so
the switching current loop runs through about 45 mm of +5V copper. The
schematic is unchanged: only the PCB shows it. Its `symptom.md` (written
for plan 013 step 3, not from a bench) is what this layout would show:
input ripple at U1's VIN and output ringing once the load passes about
1.5 A.

`board.kicad_pcb` is the planted board, kept so that the debug mode's
evidence check can hold a board-linked rework to its copper. Its layout
facts are the ones in `layout.txt`; its DRC needs the source's
`USB power port.kicad_pro` beside it for the same entries.

The layout facts the prompt gets name it: `pl1` (U1.2 VIN's nearest
decoupling capacitor, C2, 44.8 mm of copper from pad to pad; about
24 mm in a straight line) and `pl2` (U2.8 V+, C2, 30.5 mm).

The board is `usbc-buck-no-fault`'s; its `expected.md` lists what a
reviewer might wrongly flag, and `judged.md` holds its entries.

Fix: move C1-C3 back beside U1, their +5V pads within a few mm of
U1.2 and their GND pads on short vias to the ground pour.

Planted edit (`USB power port.kicad_pcb`): C1 and C2 at (117.5, 78.825)
and (119.7, 78.825), rotated 90°; C3 at (117.8, 81.7), rotated 0°. New
F.Cu +5V tracks join their pads and tee onto the +5V track from J1; new
GND vias at each cap's GND pad; the GND stubs left at the old pads
removed. KiCad 9 DRC before and after: the same 29 entries.

Source: https://github.com/maudeve-it/3.3V-3A-step-down-converter-powered-by-USB-C
at 8638856, `KiCad/USB power port.kicad_pcb`. License: MIT.

Scoring: PASS if an issue naming U1's input capacitors too far from
its VIN is in the top 3.

Auto-check: u1|c1|c2|c3|pl1; decoupl|input cap|bypass|vin; far|distance|layout|placement|copper|loop
