Root cause (planted fault):

USART1 is wired TX-to-TX and RX-to-RX. STM32 PA9 (USART1_TX) goes to the
CP2102's TXD (U22.26) and PA10 (USART1_RX) goes to its RXD (U22.25). Both
outputs drive one net and both inputs listen to the other, so nothing gets
through in either direction. Both outputs idle high, so the contention is
silent until one of them transmits.

Fix: cross the lines (PA9 -> CP2102 RXD, PA10 -> CP2102 TXD). On the bench,
cut both traces and cross them with bodge wires. As a firmware workaround,
the STM32F030's USART has a TX/RX pin-swap bit (USART_CR2 SWAP).

Planted edit (`ORBIT.kicad_sch`): the two global labels at the CP2102 side
(x = 261.62 mm) renamed: USART1_RX at y = 107.95 -> USART1_TX, and
USART1_TX at y = 110.49 -> USART1_RX. ERC before and after: identical
(118 messages).

Source: https://github.com/mohamedboubaker/Battery-Powered-STM32-Board-With-USB
at a33a81c, `Hardware/ORBIT.kicad_sch`. License: MIT.

Scoring: PASS if the TX/RX swap between the STM32 and the CP2102 is in the
top 3 hypotheses.

Auto-check: txd|tx; rxd|rx; swap|crossed|tx-to-tx|tx to tx|tied to tx|reversed
