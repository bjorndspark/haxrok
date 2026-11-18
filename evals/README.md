# Evals

Known schematic bugs, each with the symptoms someone saw on the bench and
the confirmed root cause. They answer one question: does the model find
the real bug?

## Cases

```
cases/<name>/
  symptom.md    what was observed — never the root cause; review-only
                cases have none
  board.kicad_pcb  the board, optional: debug mode checks a rework's
                tracks and vias against it (`app_prompts.py`)
  netlist.txt   compact netlist (compact_netlist.py output)
  erc.txt       KiCad ERC report, optional
  layout.txt    KiCad DRC findings and layout facts, as the prompt gets
                them from the board (`layout.format_board`), optional
  expected.md   confirmed root cause, fix, and the pass rule
  parts.txt     datasheet URLs the compact netlist drops, optional
  judged.md     each review issue judged real or wrong (`judged.py`)
```

| Case | Bug | Source |
|---|---|---|
| `rust-board-fb-swapped` | SY8088 feedback divider resistors swapped, so +3V3 comes up at 0.73 V | Planted (esp-rs/esp-rust-board) |
| `rust-board-i2c-vbus` | I²C pull-ups on VBUS instead of +3V3, so the bus fails on battery | Planted (esp-rs/esp-rust-board) |
| `rust-board-part-ratings` | Two planted edits: a 4 V capacitor on 5 V VBUS (C15) and a 100 Ω VBUS bleeder (R15) dissipating 250 mW in an 0402; the netlist carries footprints | Planted (esp-rs/esp-rust-board) |
| `rust-board-usb-no-esd` | The USB D+/D- TVS diodes (D5, D8) removed; the C3's USB pin dies after a discharge at the plug, the rest of the board runs | Planted (esp-rs/esp-rust-board) |
| `orbit-uart-swapped` | STM32 USART1 wired TX-to-TX / RX-to-RX with the CP2102 | Planted (Battery-Powered-STM32-Board-With-USB) |
| `orbit-boot0-high` | BOOT0 pull-down returned to +3.3V, so it always boots the ROM bootloader | Planted (Battery-Powered-STM32-Board-With-USB) |
| `orbit-l2-shorted` | TPS63802 L2 pin (and the inductor's L2 end) on its own output, +3.3V, so start-up needs 0.8–1.2 A from a bench supply | Planted (Battery-Powered-STM32-Board-With-USB) |
| `orbit-vdda-late` | STM32 VDDA moved onto an added RT9013 LDO enabled from +3.3V, so VDDA comes up after VDD against the datasheet | Planted (Battery-Powered-STM32-Board-With-USB) |
| `rp-micro-ldo-en` | RT9013 LDO enable tied to its own output, so it never starts | Planted (siderakb/rp-micro) |
| `rp-micro-cc-shared` | USB-C CC1 and CC2 tied together on one 5.1 kΩ pull-down, so e-marked USB-C cables get no VBUS | Planted (siderakb/rp-micro) |
| `rp-micro-no-fault` | None; the cause is a charge-only USB cable. Pass = no schematic fault claimed at any rank | Unmodified board (siderakb/rp-micro) |
| `samd21-breakout-no-fault` | None; review only, for false alarms | Unmodified board (atoomnetmarc/SAMD21-breakout) |
| `usbc-buck-no-fault` | None; review only, for false alarms | Unmodified board (maudeve-it/3.3V-3A-step-down-converter-powered-by-USB-C) |
| `usbc-buck-cin-far` | The buck's input capacitors C1-C3 moved from U1 to the USB-C connector on the PCB, 44.8 mm of copper from U1's VIN; its `symptom.md` is written, not from a bench, to check board reworks (plan 013 step 3) | Planted (maudeve-it/3.3V-3A-step-down-converter-powered-by-USB-C) |
| `moist-sensor-no-fault` | None; review only, for false alarms | Unmodified board (Miceuz/rs485-moist-sensor) |
| `furby-boot-cap` | ESP32-S3 GPIO0 has 100 nF to GND and only the internal pull-up, so it's still low when sampled and every cold boot enters download mode | Real bug (maintainer's board, built) |
| `artemia-fet-pinout` | BSS123 FETs Q1/Q2 drawn with a D-G-S symbol (BSS123 is G-S-D), so the MCU can't set or clear the boost-enable flip-flop; also 10 kΩ pull-downs on two open-drain lines with pull-ups | Real bug (patlab-ucsd/artemia_hardware, fix a05f688) |
| `enviro-node-usb-backfeed` | CP2102N VREGIN on USB VBUS and its VDD output on the board's +3.3V, so a USB cable powers the board | Real bug (DPIclimate/enviro-node, fix 1a1253b) |
| `lpddr5-fb-no-divider` | MPM54304 buck: on all four channels the divider's lower resistor runs from VOUT to GND, not FB to GND, so every output regulates at the FB reference | Real bug (antmicro/lpddr5-testbed, fix 4a6fea22) |

Planted cases take a real open-source board and make one minimal edit that
KiCad's ERC doesn't flag (ERC checked before and after). Each `expected.md`
records the exact edit, the source commit and the license. Planted faults
are cleaner than real bugs, so scores on them are not accuracy on real
boards.

Review-only cases take an unmodified open-source board of a kind the
other cases don't cover and have no `symptom.md`: `make eval-score`
reviews them, and every issue a review raises is judged in their
`judged.md`, so they measure false alarms. Their `expected.md` lists
the correct parts a reviewer might wrongly flag, the source commit and
the license.

Real-bug cases from public repos take the board at the parent of a
commit that fixes a schematic bug, unmodified. Their `expected.md` holds
the cause and the fix as the commit message or its linked issue states
them (never guessed from the diff), the source commit and the license.
A case has a `symptom.md` only when that source states what was
observed; without one it's scored in review mode only.

To add a case from a KiCad project:

```sh
kicad-cli sch export netlist -o board.net board.kicad_sch
kicad-cli sch erc -o erc.rpt board.kicad_sch
python3 -I evals/compact_netlist.py board.net > evals/cases/<name>/netlist.txt
```

Then write `symptom.md` from bench notes only, and `expected.md`.

A case with a board gets `layout.txt` from the board's DRC report and the
case's netlist (`layout_text.py`, in the backend container; the board
and report go under `data/`, which the container sees as `/app/data`):

```sh
kicad-cli pcb drc --format json --schematic-parity --severity-all -o data/drc.json board.kicad_pcb
docker compose exec -T -e PYTHONPATH=/app backend uv run --no-sync \
  python /evals/layout_text.py <name> /app/data/board.kicad_pcb /app/data/drc.json \
  > evals/cases/<name>/layout.txt
```

A planted PCB edit is checked like a schematic one: DRC before and after
gives the same entries.

Export with KiCad 9, as the backend container has (`docker compose exec
backend kicad-cli …`): KiCad 10.0.8 wrote orbit's pin functions with the
pin number appended (`L2_7` for `L2`), so the pin names no longer match
the other cases or the pin rules.

## Go/no-go

`gonogo.py` sends each case to the model under two conditions:

- **netlist**: symptoms and netlist only. This is the hard test, because
  the model has to reason about the circuit.
- **netlist+erc**: the ERC report is added too. This is realistic, because
  the tool will always have ERC.

```sh
# endpoint from AI_BASE_URL / AI_API_KEY (default Meta's API); load .env first:
#   set -a; . ./.env; set +a
python3 -I evals/gonogo.py                              # muse-spark-1.3-contributor
python3 -I evals/gonogo.py --model muse-spark-1.3
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

## Results: review mode, 2026-11-03 and 2026-11-04

`make eval-app case=<name> mode=review` sends a case with no symptoms
through the review prompts (see `DECISIONS.md`, "Review mode reuses the
debug pipeline") and writes `app-review-diagnosis.md` and
`app-review-analysis.json`. One run each, three at once.

| Case | Rank of real cause | Total time | Notes |
|---|---|---|---|
| `rp-micro-ldo-en` | #1 (won't work) | 158 s | |
| `rust-board-fb-swapped` | #1 (won't work) | 237 s | Also flagged missing crystal load caps and a WS2812 on 3V3 |
| `rp-micro-no-fault` | FAIL | 218 s | Flagged the correct 1 kΩ crystal resistor (may fail), marked unverified, fix gated on a datasheet check |

### With reference designs, 2026-11-04

After `reference.py` (see `DECISIONS.md`, "Reference designs and
calculations are verified evidence"): reference notes and calculated
values in the input.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rp-micro-no-fault` | review | PASS (no issues) | 69 s | Lists every block checked as matching the Raspberry Pi minimal design |
| `rust-board-fb-swapped` | review | #1 (won't work) | 198 s | Cites `cf1` (0.73 V); #2 is GPIO2 without the pull-up Espressif recommends |
| `rust-board-fb-swapped` | debug | #1 | 206 s | "U5 buck programmed to 0.73V" |

After `calc.py` (LED currents, I²C pull-ups, RC delays, dividers; see
`DECISIONS.md`, "Reference designs and calculations are verified
evidence"). The I²C check flags `rust-board-i2c-vbus`'s pull-ups to VBUS
(5 V) above the 3.3 V supply of every IC on the bus, before the model runs.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rust-board-i2c-vbus` | review | #1 (won't work) | 216 s | #3 calls the 32.768 kHz crystal's DNP load caps "may fail", reasoned only |
| `rust-board-i2c-vbus` | debug | #1 | 258 s | Calls it a verified design error |
| `rp-micro-no-fault` | review | PASS (no issues) | 125 s | |

After the USB-C CC check (`usb_cc`: each CC pin needs its own 5.1 kΩ
±10% to ground). On `rp-micro-cc-shared` the check flags the shared
pull-down before the model runs; on every other case it stays quiet.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rp-micro-cc-shared` | review | #1 (may fail) | 157 s | "USB-C CC1/CC2 tied together with one Rd - e-marked cables will keep VBUS off"; #2 compares the 20 pF crystal caps with the reference's 15 pF (improve) |
| `rp-micro-cc-shared` | debug | #1 | 185 s | Called a verified design error that explains every symptom |
| `rp-micro-no-fault` | review | PASS (no issues) | 85 s | |

After the decoupling check (`decoupling`: flagged only when an IC's
supply rail has no capacitor to ground). No case has such a rail, so it
stays quiet on all of them; unit tests cover the flag.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rp-micro-no-fault` | review | PASS (no issues) | 71 s | |
| `rp-micro-cc-shared` | review | #1 (may fail) | 134 s | |

After the AMS1117 and CH340 notes and the AMS1117 dropout calculation.
No case has either part, so these runs only check nothing else moved;
unit tests cover the notes and the flag.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rp-micro-no-fault` | review | PASS (no issues) | 101 s | |
| `rp-micro-cc-shared` | review | #1 (may fail) | 164 s | |

After plan 002's checks: `uart` (TX wired to TX), `enable_pin` (a
regulator's EN on its own output) and `overvoltage_pullup` (a non-I²C
pull-up above an IC's I/O supply). Run offline on every case, `uart` flags
only `orbit-uart-swapped` and `enable_pin` only `rp-micro-ldo-en`;
`overvoltage_pullup` flags nothing (no case has the fault; unit tests
cover it). `orbit-boot0-high`, the same board with the UART wired right,
stays quiet. Both planted faults were already #1, but only reasoned; now
they cite the flagged calculation.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `orbit-uart-swapped` | review | #1 (won't work) | 161 s | "UART TX tied to TX and RX tied to RX"; cites `cf1` and the USART_CR2 SWAP bit. The keyword check missed "tied to TX", now added |
| `rp-micro-ldo-en` | review | #1 (won't work) | 181 s | "U3 EN tied to its own output - regulator never starts" |
| `rp-micro-no-fault` | review | PASS (no issues) | 94 s | |

After the BOOT0 half of `enable_pin` (an STM32F030/F070 BOOT0 that reaches
a positive rail and never ground). Run offline on every case, it flags only
`orbit-boot0-high`; `orbit-uart-swapped` (BOOT0 switch to +3.3V or R2 to
GND) stays quiet. Run through the Bifrost gateway.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `orbit-boot0-high` | review | #1 (won't work) | 175 s | "BOOT0 tied high in all switch positions - boots bootloader, not flash". #2 is a false alarm: "PWR switch uses 1 and 3, middle 2 left NC" assumes a centre common, but on this K3-1270L-F1 pin 3 is the common (the BOOT0 switch on the same board shows it) |

After the switch-common rule in the debug prompt (a switch's common comes
from how the board wires it, never from a middle pin; one throw wired is an
on/off switch). Run through the Bifrost gateway; the time wasn't recorded.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `orbit-boot0-high` | review | #1 (won't work) | — | The PWR1 false alarm is gone: the diagnosis infers pin 3 as the common from PWR1's own wiring and calls it an ordinary on/off switch. #2 (may fail) is real: L1's value says 0.47 µH but its part number `DFE201612E-2R2M` is 2.2 µH, labelled unverified |

### A real bug: `furby-boot-cap`, 2026-11-12

The first case from a built board rather than a planted edit: the app's
review flagged it (run `20261112-045722-2defa1`, #1, will fail) before the
board was found to boot into download mode. Run through the gateway.

| Case | Mode | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `furby-boot-cap` | review | MISS | 250 s | The gateway refused 3 of 4 datasheet reads (503, PII detector down), so the review had no ESP32-S3 facts and called "GPIO0 via C5 to GND, internal pull-up" looks right |
| `furby-boot-cap` | review | #2 (may fail) | 291 s | All four datasheets read. Quotes the 45 kΩ pull-up, 3 ms hold time and VIH: τ = 4.5 ms, ~0.49 × VDD at 3 ms, reads low. #1 is "microphone not wired" (from the dangling-label ERC entries) |

The catch depends on the datasheet facts: without the pull-up and the hold
time the same netlist reads as correct. `mcu.py`'s strapping check ignores
capacitors, so nothing deterministic flags it yet.

## Reading the result

| Outcome | Meaning |
|---|---|
| Finds it from the netlist alone | The model can reason about circuits, so go ahead |
| Finds it only with ERC | Still go: connecting ERC warnings to symptoms is a real product |
| Misses it even with ERC | This model is too weak; try a larger one before deciding |

## Results: the app's two-step prompts, 2026-11-03

`gonogo.py` doesn't run the app's prompts. `app_prompts.py` does: it
sends a case through `prompt.DIAGNOSE` and then `prompt.STRUCTURE`
(`backend/app/features/debug/prompt.py`, see `DECISIONS.md`, "Debug
analysis is two model calls"), with the netlist and ERC as above. Since
2026-11-03 it also reads the compact netlist back into the app's `Design`
and adds the pin table and verified wiring findings the app would send
(`pins.lookup`). Since 2026-11-12 it builds the input as the app's review
does once the datasheets are read: each IC's datasheet is found (from its
value and `parts.txt`, below) and read through the gateway, its pins,
roles and typical application replace the hand-written pin table and
recheck the wiring, and the datasheet facts, deviations, limit checks and
budgeted power tree go in too. No committed case has an IC in the
hand-written pin table, so emptying it changes no case's input; what
findings there are (`orbit-l2-shorted`'s U15.7) come from the datasheet.
The results table below predates both. It runs in the backend container, so it uses the app's
gateway settings:

```sh
make eval-app case=rust-board-fb-swapped   # one case; run at most ~3 at once
```

`sim=startup` (or a comma list of `startup,load_step,dropout`) also runs
the vendor-model simulations alongside the prompts, as the app streams
them: each IC's SPICE model looked up from the same parts and datasheets,
then `spice_circuits.simulate`. A compact netlist drops the schematic's
Datasheet field, so a case gives the URLs the finders start from in
`parts.txt` (`U15 https://…` per line); without one, a part is found only
when the datasheet store (`DATASHEET_DIR`) already holds its part number,
as the orbit cases' U15 does once `orbit-l2-shorted` has run. It writes `app-simulations.json` and prints
each verdict. Switching models take minutes per run. Start-up runs at
the source's nominal, from a current-limited bench supply, and at its min
and max. Finished runs are cached in Redis (`SPICE_CACHE_TTL_S`), so a
repeat of an unchanged case reads them back in seconds.

Answers go to `results/<case>/<model>/app-diagnosis.md` and
`app-analysis.json`. The rank it prints is where the `Auto-check:`
keywords first match in the structured hypotheses. `meta/muse-spark-1.3-contributor`, one run each, three at once.
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

### With the wiring check, 2026-11-03

After `pins.check_wiring` (see `DECISIONS.md`, "Wiring faults the pin
table proves are found by code"), with the pin table and findings in the
input. The printed line now names the findings (`findings <pins>`).

| Case | Run | Rank of real cause | Total time | Notes |
|---|---|---|---|---|
| `rp-micro-no-fault` | 1 | PASS | 221 s | No finding; cable #1, #3 is assembly "despite correct schematic" |

### Through the backend from KiCad files, 2026-11-03

The first time the six planted / no-fault cases went through the real
upload path: `POST /api/v1/debug/analyze` with each board's `.kicad_pro`
and `.kicad_sch` files, so the backend parsed the schematic itself instead
of reading `netlist.txt`. The KiCad files aren't committed. They were
rebuilt from the source commit and edit in each `expected.md`, and each
rebuild exports exactly that case's `netlist.txt`. The symptoms were
rewritten one observation per line, as the app expects: the hard-wrapped
`symptom.md` splits into fragments ("Works:", half sentences) that count
as symptoms. One run each, three at once.

| Case | Rank of real cause | Label on it | Total time | Notes |
|---|---|---|---|---|
| `orbit-boot0-high` | #1 | Fits the symptoms | 254 s | #2 proposes an NRST pull-up (the STM32 has one inside), gated on a datasheet check, while saying it "does not fit" |
| `orbit-uart-swapped` | #1 | Speculative | 279 s | Labelled Speculative because it explains 4 of 12 lines, and the other 8 are setup and "Works:" lines |
| `rp-micro-ldo-en` | #1 | Fits the symptoms | 215 s | |
| `rust-board-fb-swapped` | #1 | Fits the symptoms | 207 s | |
| `rust-board-i2c-vbus` | #1 | Fits the symptoms | 247 s | Titles came back as "#1 #1 - …" |
| `rp-micro-no-fault` | MISS (cable #1) | | 225 s | #2 suspects the correct 1k R3 on XOUT, rework gated on the design-guide check; all three hypotheses are labelled "Fits the symptoms" |

All five planted faults ranked #1 from KiCad files. The no-fault case
fell back to the R3 suspicion it passed on before, so its pass is not
stable. The run also showed the evidence check stripping labels from
correct claims on boards with nets named like pin numbers (rp-micro's
`2`, `3`, `A1` matched inside `R3.2`, `J1.A1`); fixed in `24220f7`, after
these runs.

## Scorecard: every case, several runs

One run of one case can't show a run-to-run flip like `rp-micro-no-fault`'s
above. `scorecard.py` (plan 012) sends every case, or a list, through the
review prompts n times (`app_prompts.answer`, the same calls as
`make eval-app mode=review`), at most three runs at once:

```sh
make eval-score                                    # all cases, 3 runs each
make eval-score n=2 cases=rp-micro-no-fault,furby-boot-cap
```

Per case it prints the rank where the `Auto-check:` keywords first match
in each run (`miss` if none do, `err` if the run failed), each run's
issues as will fail / may fail / improve, and each run's issues judged as
bug / real / wrong / unjudged (below). A case with `Auto-check: none`
has no rank. It writes
`results/scorecard/<model>/<UTC time>/scorecard.md`, `scorecard.json` (to
diff two scorecards), `unjudged.md` and each run's diagnosis and analysis
under `<case>/run-<i>-…`.

The issues besides the planted bug are judged by each case's `judged.md`
(`judged.py`; plan 012 step 2): one entry per issue a review has raised,
`real` (a true flaw or a fair improvement of the board: the planted boards
have their own) or `wrong` (a correct part called an error, or a false
fact), matched by the parts, pins or nets the issue cites and by keywords,
in the Auto-check syntax:

```md
## SB1/SB2 short GPIO10/11 to ground
Verdict: wrong
Cites: sb1|sb2
Match: gnd|ground
Why: three-pad jumpers, pads 2-3 bridged by default; GPIO reaches the header.
```

The first entry that matches wins. An issue no entry matches is listed in
`unjudged.md` with its summary and cites, to write the next entry from;
then rescore the same sweep, no model calls:

```sh
make eval-score from=evals/results/scorecard/<model>/<UTC time>
```

`(draft)` marks an entry the agent wrote that the user hasn't confirmed;
the table counts those apart (`3+2d`: three by confirmed entries, two by
drafts). An LLM judge was rejected: its verdicts vary between runs too. Reviews skip the AI answer cache (else every run after
the first is the first run's answer); datasheets still use it. Each run
takes about 1.5–5 minutes, so a full sweep takes about 1–1.5 hours.

### Baseline, 2026-11-14

`meta/muse-spark-1.3-contributor`, review mode, every case 3 times
(`results/scorecard/meta_muse-spark-1.3-contributor/20261114T065544Z/`,
plan 012 step 5): 54 runs, 0 failed, 184 issues, every issue judged by
the Auto-check or a confirmed `judged.md` entry except 4 by drafts.
Compare a prompt change against these numbers.

Misses, by case kind:

| Cases | Runs | Bug ranked #1 | Bug ranked at all | Missed |
|---|---|---|---|---|
| Planted (10) | 30 | 21 | 30 | 0 |
| Real bug (4) | 12 | 6 | 7 | 5 |

The 5 misses are `artemia-fet-pinout` in all 3 runs (no issue names the
FETs' pinout) and `lpddr5-fb-no-divider` in 2 of 3 (no issue names the
buck's FB pins). Planted faults are cleaner than real bugs, so the
planted row is not accuracy on real boards.

Issues judged wrong, per board (the 3 or 4 cases of one board added up;
real counts drafts in brackets):

| Board | Runs | Issues | Bug | Real | Wrong |
|---|---|---|---|---|---|
| rust-board (4 planted) | 12 | 32 | 15 | 17 | 0 |
| orbit (4 planted) | 12 | 52 | 12 | 17 (+2) | 21 |
| rp-micro (2 planted, 1 unmodified) | 9 | 14 | 6 | 8 | 0 |
| `samd21-breakout-no-fault` | 3 | 13 | – | 0 | 13 |
| `usbc-buck-no-fault` | 3 | 9 | – | 5 | 4 |
| `moist-sensor-no-fault` | 3 | 7 | – | 4 | 3 |
| `furby-boot-cap` | 3 | 12 | 3 | 2 | 7 |
| `artemia-fet-pinout` | 3 | 15 | 0 | 9 (+2) | 4 |
| `enviro-node-usb-backfeed` | 3 | 15 | 3 | 11 | 1 |
| `lpddr5-fb-no-divider` | 3 | 15 | 1 | 7 | 7 |
| All | 54 | 184 | 40 | 80 (+4) | 60 |

`rust-board-part-ratings` has two planted edits, so its bug counts 2 per
run. On the 4 unmodified boards (12 runs) the reviews raised 31 issues,
8 of them will-fail; 20 of the 31 are wrong.

### With the board's DRC and layout facts, 2026-11-17

The same sweep after plan 013 step 2 gave the model the KiCad DRC
findings and layout facts (`layout.txt`) on the two `usbc-buck` boards
(`results/scorecard/meta_muse-spark-1.3-contributor/20261117T141005Z/`):
55 runs, 2 failed (`orbit-l2-shorted` runs 2 and 3), 199 issues, 15
unjudged.

Misses, by case kind:

| Cases | Runs | Bug ranked #1 | Bug ranked at all | Missed |
|---|---|---|---|---|
| Planted (11) | 33 | 22 | 30 | 1 |
| Real bug (4) | 12 | 7 | 8 | 4 |

`usbc-buck-cin-far` ranked its bug #4, #2 and #2: below the wrong
will-fail "CC REF" issue the unmodified board also gets, at the top of
the may-fail issues. The 1 planted miss is `rust-board-fb-swapped` run
2, an Auto-check miss: its #1 issue names U5's 0.73 V output but none of
"divider", "ratio", "resistor" or "swap". The real-bug misses are
`artemia-fet-pinout` in all 3 runs and `lpddr5-fb-no-divider` in 1, as
in the baseline.

Issues judged wrong, per board:

| Board | Runs | Issues | Bug | Real | Wrong |
|---|---|---|---|---|---|
| rust-board (4 planted) | 12 | 39 | 14 | 24 | 0 |
| orbit (4 planted) | 12 | 38 | 10 | 13 (+1) | 14 |
| rp-micro (2 planted, 1 unmodified) | 9 | 13 | 6 | 6 | 1 |
| `samd21-breakout-no-fault` | 3 | 12 | – | 0 | 12 |
| `usbc-buck-no-fault` | 3 | 12 | – | 9 | 3 |
| `usbc-buck-cin-far` | 3 | 13 | 3 | 6 | 4 |
| `moist-sensor-no-fault` | 3 | 14 | – | 5 | 3 |
| `furby-boot-cap` | 3 | 13 | 3 | 3 | 5 |
| `artemia-fet-pinout` | 3 | 15 | 0 | 7 (+2) | 3 |
| `enviro-node-usb-backfeed` | 3 | 15 | 3 | 9 | 2 |
| `lpddr5-fb-no-divider` | 3 | 15 | 2 | 5 | 6 |
| All | 55 | 199 | 41 | 87 (+3) | 53 |

On the 4 unmodified boards (12 runs) the reviews raised 38 issues, 8 of
them will-fail; 18 of the 38 are wrong and 6 unjudged.
`usbc-buck-no-fault` gains one may-fail issue per run (#3, #4, #3): its
decoupling distances, which the layout facts flag (U1.2 at 5.5 mm, U2.8
at 18 mm). They are true, so `judged.md` counts them real (the table
above is re-judged with that entry).

### With board reworks, 2026-11-18

The same sweep after plan 013 step 3 let a debug-mode rework step name
the board's tracks and vias
(`results/scorecard/meta_muse-spark-1.3-contributor/20261117T181127Z/`):
57 runs, 0 failed, 195 issues, 13 unjudged. Review mode's prompts and
code path didn't change (a review's rework is empty), so the
differences from the sweep above are run-to-run variation.

Misses, by case kind:

| Cases | Runs | Bug ranked #1 | Bug ranked at all | Missed |
|---|---|---|---|---|
| Planted (11) | 33 | 22 | 32 | 1 |
| Real bug (4) | 12 | 5 | 5 | 7 |

`usbc-buck-cin-far` ranked its bug #2, missed and #2; the miss is an
Auto-check miss: run 2's #3 issue, "Switching and op-amp supply caps far
from pins", cites `pl1` and U1.2 but none of "decoupl", "input cap",
"bypass" or "vin". The real-bug misses are `artemia-fet-pinout` in all 3
runs, `enviro-node-usb-backfeed` in 2 (#1, #1, #4 above) and
`lpddr5-fb-no-divider` in 2 (1 above).

Issues judged wrong, per board:

| Board | Runs | Issues | Bug | Real | Wrong |
|---|---|---|---|---|---|
| rust-board (4 planted) | 12 | 31 | 15 | 16 | 0 |
| orbit (4 planted) | 12 | 46 | 12 | 16 | 16 |
| rp-micro (2 planted, 1 unmodified) | 9 | 13 | 6 | 6 | 1 |
| `samd21-breakout-no-fault` | 3 | 12 | – | 0 | 12 |
| `usbc-buck-no-fault` | 3 | 15 | – | 8 | 3 |
| `usbc-buck-cin-far` | 3 | 13 | 2 | 5 | 4 |
| `moist-sensor-no-fault` | 3 | 10 | – | 5 | 4 |
| `furby-boot-cap` | 3 | 10 | 3 | 2 | 4 |
| `artemia-fet-pinout` | 3 | 15 | 0 | 11 (+3) | 0 |
| `enviro-node-usb-backfeed` | 3 | 15 | 1 | 9 | 3 |
| `lpddr5-fb-no-divider` | 3 | 15 | 1 | 8 | 6 |
| All | 57 | 195 | 40 | 86 (+3) | 53 |

On the 4 unmodified boards (12 runs) the reviews raised 37 issues, 9 of
them will-fail; 19 of the 37 are wrong and 5 unjudged.

Debug mode, where a rework can name the board's copper: `make eval-app
case=usbc-buck-cin-far`, 3 runs with the AI answer cache off
(`AI_CACHE_TTL_S=0`; with it on, the 2nd and 3rd runs return the 1st's
answer). The bug ranked #1 in all 3. Their 5 rework steps tack a
capacitor at U1, reflow joints, swap L1 and wire J4 to the loads apart:
none is a cut, a jumper or a drilled via, so none is linked to the
board and none rejected. The board check is covered by tests only.
