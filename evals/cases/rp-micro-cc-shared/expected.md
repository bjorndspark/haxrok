Root cause (planted fault):

The USB-C receptacle's CC1 (J1.A5) and CC2 (J1.B5) are tied together and
share one 5.1 kΩ pull-down, R6. A USB-C sink needs its own 5.1 kΩ Rd on
each CC pin. The laptop charger's 100 W cable is e-marked: it puts its
~1 kΩ Ra on the plug's VCONN pin, which here lands on the same node as R6,
so the source sees Ra instead of Rd and never turns on VBUS. USB-A to
USB-C cables (VBUS is always on) and plain USB-C cables (no Ra) still
work, which is why it looks like a cable or charger fault.

Fix: give CC2 its own 5.1 kΩ to ground. On the bench, cut the link
between J1.A5 and J1.B5 and fit a 5.1 kΩ 0402 from J1.B5 to GND.

Planted edit (`rp-micro.kicad_sch`): removed R7 (5.1k, CC2 pull-down),
its `symbol_instances` entry and its two GND-side wires ((187.96, 34.29)
to (194.31, 34.29), and (194.31, 34.29) to (194.31, 33.02)); added a wire
from R7's former CC2 end (180.34, 34.29) up to R6's CC1 end
(180.34, 31.75) with a junction there. ERC before and after: the same
set of messages, so `erc.txt` is the no-fault case's.

Source: https://github.com/siderakb/rp-micro at a7baf36,
`hardware/rp-micro.kicad_sch`. License: CERN-OHL-P-2.0.

Scoring: PASS if CC1 and CC2 sharing one pull-down (CC2 without its own
5.1 kΩ) is in the top 3 hypotheses.

Auto-check: cc; cc2|share|shared|tied|own|each|separate; 5.1|5k1|rd|pull-down|pulldown
