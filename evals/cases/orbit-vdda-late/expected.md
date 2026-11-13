Root cause (planted fault):

U16's VDDA (U16.9) comes up after its VDD (U16.1, 24, 48). The
STM32F030 requires VDDA first: "During the startup phase it is required
that VDDA should arrive first and be greater than or equal to VDD"
(datasheet DS9773, p.13). VDDA is now made by U23 (RT9013-33) from
+BATT, but U23's EN is on +3.3V, so U23 only starts once +3.3V is
already up: the wiring fixes the wrong order. While VDD leads, VDDA is
below VDD, outside what the datasheet allows, so start-up isn't
guaranteed; RESET, pressed with both rails up, always works. Steady
state is also marginal: two 3.3 V regulators with their own
tolerances don't keep VDDA >= VDD.

Fix: put VDDA back on +3.3V (through a ferrite if the filtering was the
point), or make VDDA's regulator lead: EN on +BATT and +3.3V's
regulator (U15) enabled from VDDA or U23's power-good. On the bench,
lift U23's EN pin and tie it to +BATT.

Planted edit (`ORBIT.kicad_sch`): removed the wire joining VDDA's stub
(123.19, 39.37 mm) to the +3.3V VDD group and put a `VDDA` label on
it; added U23 (RT9013-33GB, LCSC C47773, SOT-23-5) at (69.85, 147.32
mm) with VIN on +BATT, EN on +3.3V, GND, VOUT on `VDDA`, and 1 uF caps
C25 (input) and C26 (output), like the board's C23. ERC before and
after, through the backend container's kicad-cli (KiCad 9.0.2): 208 ->
218 messages; the new ones are library-configuration warnings for the
added parts, and +BATT's one "input power pin not driven" error moves
from U1.3 to U23.1 (KiCad reports one per net). `erc.txt` is that
container's report. The unmodified board is the fixed variant: VDDA
and VDD share +3.3V and the rail-order check stays silent.

Symptoms: made up to fit the fault, not from hardware. The 3.29 V
VDDA reading is within the RT9013's 2 % output tolerance.

Source: https://github.com/mohamedboubaker/Battery-Powered-STM32-Board-With-USB
at a33a81c, `Hardware/ORBIT.kicad_sch`. License: MIT.

Scoring: PASS if VDDA (U16.9) coming up after VDD because U23's EN is on
+3.3V is in the top 3 hypotheses. Review mode: PASS if it's listed as
"won't work as drawn" or "may fail".

Auto-check: vdda; u23|rt9013|ldo|regulator|en; first|order|after|before|sequenc
