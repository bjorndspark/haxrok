# Evals

Known schematic bugs, each with the symptoms someone saw on the bench and
the confirmed root cause. They answer one question: does the model find
the real bug?

## Cases

```
cases/<name>/
  symptom.md    what was observed — never the root cause
  netlist.txt   compact netlist (compact_netlist.py output)
  erc.txt       KiCad ERC report, optional
  expected.md   confirmed root cause, fix, and the pass rule
```

| Case | Bug | Source |
|---|---|---|
| `rust-board-fb-swapped` | SY8088 feedback divider resistors swapped, so +3V3 comes up at 0.73 V | Planted (esp-rs/esp-rust-board) |
| `rust-board-i2c-vbus` | I²C pull-ups on VBUS instead of +3V3, so the bus fails on battery | Planted (esp-rs/esp-rust-board) |
| `orbit-uart-swapped` | STM32 USART1 wired TX-to-TX / RX-to-RX with the CP2102 | Planted (Battery-Powered-STM32-Board-With-USB) |
| `orbit-boot0-high` | BOOT0 pull-down returned to +3.3V, so it always boots the ROM bootloader | Planted (Battery-Powered-STM32-Board-With-USB) |
| `rp-micro-ldo-en` | RT9013 LDO enable tied to its own output, so it never starts | Planted (siderakb/rp-micro) |
| `rp-micro-no-fault` | None; the cause is a charge-only USB cable. Pass = no schematic fault claimed at any rank | Unmodified board (siderakb/rp-micro) |

Planted cases take a real open-source board and make one minimal edit that
KiCad's ERC doesn't flag (ERC checked before and after). Each `expected.md`
records the exact edit, the source commit and the license. Planted faults
are cleaner than real bugs, so scores on them are not accuracy on real
boards.

To add a case from a KiCad project:

```sh
kicad-cli sch export netlist -o board.net board.kicad_sch
kicad-cli sch erc -o erc.rpt board.kicad_sch
python3 -I evals/compact_netlist.py board.net > evals/cases/<name>/netlist.txt
```

Then write `symptom.md` from bench notes only, and `expected.md`.

## Go/no-go

`gonogo.py` sends each case to the model under two conditions:

- **netlist**: symptoms and netlist only. This is the hard test, because
  the model has to reason about the circuit.
- **netlist+erc**: the ERC report is added too. This is realistic, because
  the tool will always have ERC.

```sh
# gateway from AI_BASE_URL / AI_API_KEY (default localhost:8080); load .env first:
#   set -a; . ./.env; set +a
python3 -I evals/gonogo.py                              # meta/muse-spark-1.3-contributor
python3 -I evals/gonogo.py --model meta/muse-spark-1.3
python3 -I evals/gonogo.py --case rust-board-fb-swapped
```

Answers are written to `results/<case>/<model>/<condition>.md`, which is
gitignored. The PASS?/MISS? it prints only checks the keywords on the
`Auto-check:` line of the case's `expected.md` (`;` between groups that
must all match, `|` between alternatives; `none` prints READ), so read
each answer to judge it.

## Results: planted cases, 2026-11-02

`meta/muse-spark-1.3-contributor`, one run per case and condition, each
answer read by hand. Planted faults are cleaner than real bugs: one edit,
honest symptoms written knowing the cause. This shows the model can reason
across different boards and fault kinds, not how accurate it is on real
boards.

| Case | Condition | Rank of real cause | Time | Notes |
|---|---|---|---|---|
| `rust-board-fb-swapped` | netlist | #1 | 166 s | Computed 0.6 × (1 + 22.1/100) = 0.73 V and matched it to the measurement |
| `rust-board-fb-swapped` | netlist+erc | #1 | 120 s | Same |
| `rust-board-i2c-vbus` | netlist | #1 | 98 s | Worked out the on-battery idle level (~2.7 V) through R15; explained the 3.7 V clamp on USB |
| `rust-board-i2c-vbus` | netlist+erc | #1 | 104 s | Same; #3 (VBUS-detect firmware) is a stretch |
| `orbit-uart-swapped` | netlist | #1 | 93 s | Labelled symptom measurements CONFIRMED (the prompt reserves it for netlist/ERC) |
| `orbit-uart-swapped` | netlist+erc | #1 | 121 s | Clean |
| `orbit-boot0-high` | netlist | #1 | 94 s | Explained why it runs under the debugger. #2 suggests an external NRST pull-up (the STM32 has an internal one) |
| `orbit-boot0-high` | netlist+erc | #1 | 84 s | #2 is an `nBOOT_SEL` option byte, which the STM32F030 doesn't have: hallucinated part fact |
| `rp-micro-ldo-en` | netlist | #1 | 71 s | Used the bench-supply latch-on to confirm. EN threshold given as 1.2–1.5 V |
| `rp-micro-ldo-en` | netlist+erc | #1 | 76 s | EN threshold given as 0.9–1.1 V: the two runs disagree, so neither is from a pin table |
| `rp-micro-no-fault` | netlist | MISS (cable #1) | 83 s | #2 invents a schematic fault: calls the 1k series R3 on XOUT a design error ("remains even with a good cable"). The Pico reference design uses the same 1k |
| `rp-micro-no-fault` | netlist+erc | MISS (cable #1) | 93 s | Same invented XOUT fault at #2, with a rework to short R3 |

All five planted faults were ranked #1 in both conditions. ERC added
nothing, as expected for faults chosen to be invisible to it. The no-fault
case fails in both conditions: the cable is #1, but both runs also call a
correct part a design error and recommend reworking it, which is the trust
failure the case exists to catch. Hallucinated part facts showed up in 3
of 12 answers; this is the gap the pin-function lookup in plan 001 is
meant to close.

### With the unverified-facts rule

The system prompt now says recalled part facts are UNVERIFIED, must name
the datasheet section that settles them, and can't on their own justify
calling the design wrong or recommending rework (see `DECISIONS.md`,
"Datasheet facts come from memory until retrieval"). Same model, one run
each:

| Case | netlist | netlist+erc | Notes |
|---|---|---|---|
| `rust-board-fb-swapped` | #1 | #1 | The fix now waits on checking the SY8088 FB reference |
| `rust-board-i2c-vbus` | #1 | #1 | |
| `orbit-uart-swapped` | #1 | #1 | |
| `orbit-boot0-high` | #1 | #1 | The made-up `BOOT_SEL` option byte is back at #2, but marked UNVERIFIED with RM0091 named to check it |
| `rp-micro-ldo-en` | #1 | #1 | No made-up EN threshold numbers this time |
| `rp-micro-no-fault` | MISS (cable #1) | PASS | netlist: #2 still calls the 1k R3 on XOUT "an anomaly" and proposes shorting it, though only after a datasheet check. netlist+erc: opens with "schematic looks correct" and ranks cable, solder and crystal assembly; R3's value stays an open question with no rework |

Runs also got faster: 52–90 s, against 71–166 s before. The rule stopped
unconditional rework of correct parts, but not the model suspecting them.
One pass in two on the no-fault case is not a fix, and it's one run each.

## Reading the result

| Outcome | Meaning |
|---|---|
| Finds it from the netlist alone | The model can reason about circuits, so go ahead |
| Finds it only with ERC | Still go: connecting ERC warnings to symptoms is a real product |
| Misses it even with ERC | This model is too weak; try a larger one before deciding |

## Results: the app's two-step prompts, 2026-11-03

`gonogo.py` doesn't run the app's prompts. These runs sent each case
through `prompt.DIAGNOSE` and then `prompt.STRUCTURE`
(`backend/app/features/debug/prompt.py`, see `DECISIONS.md`, "Debug
analysis is two model calls"). The netlist and ERC are as above, with no
pin table. `meta/muse-spark-1.3-contributor`, one run each, three at once.
Ranks are read from the structured answer.

| Case | Rank of real cause | Total time |
|---|---|---|
| `rust-board-fb-swapped` | #1 | 289 s |
| `rust-board-i2c-vbus` | #1 | 312 s |
| `orbit-uart-swapped` | #1 | 343 s |
| `orbit-boot0-high` | #1 | 311 s |
| `rp-micro-ldo-en` | #1 | 216 s |
| `rp-micro-no-fault` | PASS | 295 s |

The no-fault answer opens with "Schematic looks correct", ranks the cable
#1, and says to leave R3/C3/C4 alone until the crystal section of the
RP2040 design guide is checked. On a private real board (not in the
repo), through `POST /api/v1/debug/analyze` with its bench notes, the
real fault ranked #1 in 2 of 2 runs. The one-call structured prompt
before this missed it in 2 of 2.
