Root cause (planted fault):

BOOT0 (U16.44) can never be low. The slide switch selects between +3.3V
directly (switch pin 2) and R2 (switch pin 1), but R2 now returns to +3.3V
instead of GND. In both positions BOOT0 is high, so the STM32F030 boots the
ROM system bootloader (0x1FFFxxxx) instead of user flash.

SWD still works because the debug port doesn't care about boot mode. In a
debug session the IDE sets the PC to Reset_Handler directly, so main()
runs, but the Cortex-M0 has no VTOR and the vector table at 0x0 is still
aliased to system memory: the first SysTick interrupt (HAL_Delay) jumps
into the bootloader.

Fix: return R2 to GND, so the switch selects 3.3 V (bootloader) or a 10k
pull-down (user flash). On the bench, lift the R2 pad on the +3.3V side and
wire it to GND.

Planted edit (`ORBIT.kicad_sch`): the power symbol on R2's free end
(#PWR07 at 59.69, 36.83 mm) changed from `power:GND` (mirror x) to
`power:+3.3V`, and its Value from GND to +3.3V. ERC before and after: same
118 messages; only one "symbol modified in library" warning names +3.3V
instead of GND.

Source: https://github.com/mohamedboubaker/Battery-Powered-STM32-Board-With-USB
at a33a81c, `Hardware/ORBIT.kicad_sch`. License: MIT.

Scoring: PASS if BOOT0 being held high (R2 to +3.3V instead of GND) is in
the top 3 hypotheses.

Auto-check: boot0; r2|pull-down|pulldown|high|3.3
