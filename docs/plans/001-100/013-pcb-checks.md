# Plan 013: PCB checks

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-17 | 2026-11-18 | done | — | `001-mvp-scope.md` (sends only `.kicad_pro` and `*.kicad_sch`; "the PCB stays here"), `012-benchmark-scorecard.md` (checked a board's pad nets against its `.kicad_pcb` by hand), `DECISIONS.md` ("No auth": runs are recorded on disk) |

The app reviews and debugs from the schematic alone. Since plan 001 the
frontend has sent only the `.kicad_pro` and `*.kicad_sch` files, so the
`.kicad_pcb` never leaves the user's machine. The prompt and the rework
schema (`schemas.py`, `Rework`) tell the model there's no PCB data, so it
never names a track or a via.

That misses a whole class of faults a bench symptom can come from:

- **The board doesn't match the schematic.** A footprint was swapped, a
  net was renamed on one side, or a part is missing from the board. Plan
  012 found one by hand: the buck board's CC divider had to be checked
  against the `.kicad_pcb` pad nets. KiCad's DRC with schematic parity
  finds these without a model.
- **The board is unfinished or shorted.** Unrouted connections, and
  copper of two nets touching. DRC reports both.
- **The layout is wrong for the circuit.** The decoupling capacitor sits
  far from its pin, a track is too narrow for the current the power
  budget gives it, or there's no ground zone under a switcher. Only facts
  computed from the board can show these, and the model needs them as
  citable facts, the way it cites `facts.py` and `power.py` today.

Three constraints shape it:

- **The PCB is the most sensitive file in the project.** Every run is
  recorded on disk (`DECISIONS.md`, "No auth"), so sending it changes
  what a deploy keeps. The UI promises "the PCB stays here" today.
- **KiCad versions.** The backend container runs `kicad-cli` 9.0.2,
  while this machine's KiCad is 10.0.8. A board saved in KiCad 10 may not
  load in KiCad 9.
- **Size.** A board with filled zones can be several MB, and an upload is
  capped at `DEBUG_MAX_UPLOAD_BYTES` (20 MB). The model can't take a raw
  `.kicad_pcb`, so it gets computed facts, never the file.

| Step | What | |
|---|---|---|
| 1 | An opt-in PCB upload runs KiCad's DRC with schematic parity, and its findings are listed next to ERC's | done |
| 2 | The model reads the DRC findings and computed layout facts, and cites them | done |
| 3 | Rework can name tracks and vias | done |

## 1. An opt-in PCB upload runs KiCad's DRC with schematic parity, and its findings are listed next to ERC's: done

- First, try a KiCad 10 board in the container's `kicad-cli` 9.0.2
  (`pcb drc`). What it does decides how a newer board is handled.
- A toggle on the upload screen ("also send the PCB") adds the project's
  `.kicad_pcb` to the files `files.ts` sends. The "the PCB stays here"
  copy changes to match the toggle.
- `kicad.py` runs `kicad-cli pcb drc --format json --schematic-parity
  --severity-all` when a board was sent, beside `sch erc`.
- A `DrcEntry` (like `ErcEntry`) per finding, in the summary the SSE
  stream sends first, listed in the UI as "KiCad DRC findings".
- The model doesn't see them yet (step 2), so no prompt change and no
  eval run.

Decided (2026-11-17): the toggle starts off, and the browser remembers
the user's choice (`localStorage`, per-viewer convenience). Today's
"the PCB stays here" holds for anyone who never turns it on. Rejected:
off on every load (tedious for a team that always sends the board); on
whenever a `.kicad_pcb` exists (breaks the privacy default plan 001 set).
Decided (2026-11-17): only electrical DRC findings are kept: schematic
parity, unconnected items, shorts, and clearance, track, via and hole
errors. Silkscreen, courtyard, text and footprint-library findings are
dropped in the parser, the way ERC's missing-library warnings are.
Rejected: every finding, grouped (dozens of cosmetic entries per board
bury the few that explain a symptom); parity only (misses unrouted
connections and shorts, which DRC finds for free).
Decided (2026-11-17): a board `kicad-cli` 9.0.2 can't load doesn't fail
the run: the run continues schematic-only and the UI says the board was
saved in a newer KiCad, so DRC was skipped. Moving the container to
KiCad 10 is a plan of its own if the test shows it's needed (it changes
the netlist format and every committed eval case). Rejected: upgrading
in this step (re-exports every eval case and re-runs the baseline);
failing the run (punishes a user for having the current KiCad).
Decided (2026-11-17), defaults: the run record keeps the board as it
keeps the schematics, by name and SHA-256 only (`record.py`), plus the
DRC entries in the events; the board's bytes live only in the temp dir
`kicad.py` already uses. DRC entry ids are `drc-1`, `drc-2`, … like
`erc-N`. `DECISIONS.md` gets a "The PCB is opt-in" entry in this step's
commit.

Decided (2026-11-17), in the build: a `footprint_symbol_mismatch` whose
two footprints differ only in the library nickname (`Footprints:X` on the
board, `Valve:X` on the symbol) counts as a footprint-library finding and
is dropped; one that names another footprint is kept. On KiCad's
`ecc83-pp` demo, 5 of its 6 mismatches were nickname-only.

**Checks:** a KiCad 10 board (KiCad's `ecc83-pp` demo, upgraded with the
host's `kicad-cli` 10.0.8) in the container's `kicad-cli` 9.0.2: `pcb drc`
exits 3 with "Failed to load board", so the decided skip-and-continue
holds; `kicad.py` reads the board's `generator_version` to say which KiCad
saved it. `backend/tests/golden/drc_kicad9.json` is the 9.0.2 report on
the demo board with R1 deleted. In `tests/test_debug.py`, the parser keeps
`drc-1` `missing_footprint` and `drc-2` `track_dangling` from it and drops
the rest; a mismatch naming another footprint is kept; the skip reason
names KiCad 10.0. `tests/test_debug_service.py` gains 4 tests: the board
is the one named after the root sheet; a project without a board has no
board and no DRC, as before; a sent board's findings are in the summary
and the board in `record.file_digests`; a board DRC skipped says why and
the run goes on. `present.test.ts`: the project's board is kept and
another board dropped; `runFiles` sends the board only when ticked and the
review file only in review mode. In headless Chrome on the dev server, the
R1-less board with the box ticked shows "KiCad DRC findings (2)" with
drc-1 "Missing footprint R1 (1.5K)", and `0 ERC · 2 DRC` in the counts;
the KiCad 9 schematic with the KiCad 10 board shows "KiCad DRC skipped"
and the reason. `make lint` and `make test` passed (526 backend, 61
frontend), each sub-target run on its own; ESLint's one warning is in
`shared/ui/button.tsx`, untouched. Bugs found: `projectName` took the
first file's name, which with the board sorted first was
`board.kicad_pcb`; it takes the first sheet's now. `mypy` caught an
unnarrowed regex match in the parser.

## 2. The model reads the DRC findings and computed layout facts, and cites them: done

- DRC entries become citable evidence (`drc-3`), as ERC entries are.
- A parser for the `.kicad_pcb` (pads, tracks, vias, zones) turns the
  board into a few kinds of layout facts, each with an id the model
  cites: decoupling capacitor to pin distance along the track, narrowest
  track width on a net against the current `power.py` gives it, and
  whether a ground zone covers a part.
- The evidence check (`evidence.py`) accepts claims that cite them.
- A prompt and schema change: runs `make eval-score` against the
  baseline in `evals/README.md`.

**Checks:** parser tests on a committed board; `make eval-score` misses
and wrong issues against the baseline.

Decided (2026-11-17): the code judges each layout fact against a fixed
limit, and a fact past it reads "may fail, not verified", the way the
power budget does. Every fact gives its number, so the model can cite a
good layout to rule a cause out. Rejected: numbers only (the model's
judgement varies run to run and can't be unit-tested); only the facts
past a limit (the model can't rule a cause out).
Decided (2026-11-17): a board the container's `kicad-cli` 9.0.2 can't
load still gets layout facts: the app's own parser reads only pads,
tracks, vias and zones, whose form is the same in KiCad 9 and 10. DRC
stays skipped for it, as step 1 decided. Tests cover the `ecc83-pp` demo
saved in each version. Rejected: no layout facts when DRC is skipped
(this machine's KiCad 10.0.8 boards would get nothing).
Decided (2026-11-17): two board-backed eval cases. An eval case gains a
`layout.txt` (the DRC and layout facts text the prompt gets). A planted
case moves `usbc-buck`'s input capacitor 20 mm from the buck's VIN in
the source repo's `.kicad_pcb`; `usbc-buck-no-fault` gets its board, to
catch false layout alarms. The baseline in `evals/README.md` gains both.
Rejected: a regression run only (nothing measures whether the model uses
layout facts); the no-fault board only (nothing the model should find).
Decided (2026-11-17): the SSE summary carries the layout facts, and the
UI lists them as "Layout facts (N)" under the KiCad DRC findings, the
may-fail ones first. A cite chip (`pl3`) links to its row, the way
`erc-N` does. Rejected: cite chips only (a may-fail fact the model
didn't cite never reaches the user).
Decided (2026-11-17), defaults: cite kinds `drc` (`drc-3`) and `layout`
(`pl1`, `pl2`, …), and a claim resting on either has evidence `board`;
the frontend's `types.ts` gains the same values. `board.py` parses the
`.kicad_pcb` (pads, tracks, vias, zone fills, the stackup's copper
thickness) and `layout.py` computes the facts, both pure functions like
`power.py`. Decoupling: per IC supply pin (`power.py`'s load pins), the
nearest capacitor between that pin's net and GND, and the copper path
from its pad to the pin's pad along tracks and vias; when a zone is the
only link, the straight-line distance, said so. Its limit is a path over
5 mm. Track width: only on a rail whose budget has a known load; its
limit is IPC-2221 at a 10 °C rise (k = 0.048 external, 0.024 internal)
with the stackup's copper thickness, else 35 µm. Ground zone: a switcher
is an IC with an `SW`/`LX` pin (`power.py`'s `_SW_PIN`); its limit is no
GND zone fill on an adjacent layer covering the IC's and its inductor's
pads. On a board saved with unfilled zones, the ground zone fact says
the coverage isn't known and doesn't flag.

Decided (2026-11-17), in the build: a supply pin is every IC pin on a
rail that `is_supply_pin` accepts (`VIN`, `V+`, `VDD`, …), not only the
power tree's load pins: a regulator the tree doesn't recognise as a load
still needs its input capacitor, and `usbc-buck`'s U1 was missing from
the tree's loads.
Decided (2026-11-17), in the build: the copper path runs over a graph of
pads, track ends and vias (`layout._paths`), where a filled zone joins
everything it touches in a straight line. A fill counts as touching a
pad within 0.6 mm of the pad's edge (`_RELIEF_MM`), past KiCad's default
0.5 mm thermal-relief gap.
Decided (2026-11-17), in the build: the ground zone limit is 75% of the
area under the IC's and its inductor's pads (`GROUND_FILL_PCT`),
measured on a 0.1 mm grid (`_GRID_MM`). Rejected: any fill at all (a
pour that clips one pad corner would pass).
Decided (2026-11-17), in the build: the facts are numbered decoupling,
then ground zone, then track width. Track widths need the datasheets'
budgets, so the summary has none; the `datasheet_facts` event carries
the full `layout` list and goes out whenever it differs from the
summary's.
Decided (2026-11-17), in the build: a board that `board.parse` can't
read costs only its layout facts: the run goes on, DRC stands, and the
log says why (`service._parse_board`).
Decided (2026-11-17), in the build: the planted case is
`usbc-buck-cin-far`. Its edit moves all three input capacitors C1-C3
beside J1, not one capacitor 20 mm away: with C3 left in place the
nearest one stays 5.5 mm from U1's `VIN`. U1.2 is now 44.8 mm of copper
from C2 (about 24 mm in a straight line), and KiCad 9 DRC gives the same
29 entries before and after.
Decided (2026-11-17), in the build: an eval case's `layout.txt` comes
from `evals/layout_text.py`, which runs the app's `layout.check` and
`layout.format_board` on the case's netlist. Its power tree has no
budgets, so eval cases get no track width facts.
Decided (2026-11-17), in the build: under a claim, the UI lists the ERC,
DRC and layout entries it cites (`citedEntries` in `present.ts`), so a
cite chip shows its text without scrolling to the list.
Decided (2026-11-17), in the build: a gap left for later: the TPS54331's
switch pin is `PH`, which `power.py`'s `_SW_PIN` (`LX\d*|SW\d*`) doesn't
match, so `usbc-buck` gets no ground zone fact.
Decided (2026-11-18): the 5 mm decoupling limit stays, and
`usbc-buck-no-fault`'s decoupling issue (5.5 mm and 18 mm, flagged) is
judged real in its `judged.md`: the facts are true and the issue is
may-fail. Rejected: judging it wrong and raising the limit, or not
flagging capacitors of non-switching parts (an 18 mm op-amp supply path
is a real weakness).

**Checks:** `backend/tests/golden/ecc83_kicad9.kicad_pcb` and
`ecc83_kicad10.kicad_pcb` are KiCad's `ecc83-pp` demo saved by each
version; in `tests/test_debug_board.py` (4 tests) both parse to the same
33 pads, 59 tracks and one filled GND zone on B.Cu.
`tests/test_debug_layout.py` (17 tests, hand-built boards) covers a
decoupling path through a T-junction (13.0 mm, flagged), across a zone,
past a thermal-relief gap, with no copper and with an unfilled zone;
ground fill at 100%, partial and none; IPC-2221 at 1 A (0.30 mm outside,
0.78 mm inside) and a narrow budgeted track numbered after the
decoupling fact; and `format_board`. `tests/test_debug.py` gains 4
evidence tests: a `pl` cite vouches for its pins and net, a `drc` cite
keeps the label, an unknown `pl9` or `drc-9` is downgraded, and a board
claim citing only `erc` is downgraded. `tests/test_debug_service.py`
gains 2: a sent board reaches the prompt and the record (an older record
without `drc` and `layout` still loads), and a board that doesn't parse
costs only its layout facts. `make lint` and `make test` passed (553
backend, 65 frontend), each sub-target run on its own. `make eval-score`
(`evals/README.md`, "With the board's DRC and layout facts") against
the 2026-11-14 baseline: planted cases ranked the bug #1 in 22 of 33
runs (21 of 30 before), `usbc-buck-cin-far` at #4, #2 and #2 citing
`pl1`/`pl2`; the 1 planted miss is an Auto-check miss in
`rust-board-fb-swapped`; real bugs 7 #1 and 4 missed (6 and 5 before);
53 issues judged wrong (60 before), 18 of 38 on the no-fault boards (20
of 31 before); 33 claims rest on `board` evidence. The new false-alarm
risk is `usbc-buck-no-fault`'s decoupling issue, raised as may-fail in
all 3 runs from the flagged 5.5 mm and 18 mm facts; the facts are true,
so its `judged.md` counts it real.
Bugs found: `usbc-buck`'s U1 wasn't a load in the power tree, so it had
no decoupling fact (hence every supply pin, above); `plant.py` looked a
reference up wrongly; mypy caught two loop variables reused with another
type (`board.py`, `layout.py`) and `_parse_board`'s `str | None` path;
ruff B023 caught `amps` bound late in a closure; the exact-event test of
`datasheet_facts` lacked the new `layout` key.

## 3. Rework can name tracks and vias: done

- `Rework` allows a track cut, a jumper wire between two pads, and a via
  to drill out, each tied to a track or pad the board has.
- The evidence check rejects a track or via the board doesn't have.

**Checks:** schema and evidence tests; `make eval-score` against the
baseline.

Decided (2026-11-18): pads anchor a board rework. `ReworkStep` gains an
optional `board` field: `action` (`cut`, `jumper` or `drill_via`), `net`,
`from_pad` and `to_pad` (`U1.2`). The evidence check accepts a `cut` or
`drill_via` when a track or via on that net lies on the copper path
between the two pads (`layout._paths`), and a `jumper` when both pads are
on the board. Rejected: listed track ids (`t12`, `v3`; a real board's
hundreds of segments cost prompt on every run); free text only (a cut
can't be checked against the copper).
Decided (2026-11-18): a rework the evidence check rejects stays in the
fix without its `board` link; the UI marks it "not on the board" with
the reason, and the run's problems record it, the way a downgraded
claim is kept. Rejected: dropping the step (one wrong pad loses a good
idea); `practical: false` (mixes "can't do by hand" with "not on the
board").
Decided (2026-11-18): `make eval-score` runs review mode only, where
rework is empty, so `usbc-buck-cin-far` gains a `symptom.md` and
`make eval-app case=usbc-buck-cin-far` runs 3 times in debug mode,
counting the board reworks and how many pass the evidence check.
`make eval-score` still runs as a regression against the baseline.
Rejected: `make eval-score` only (nothing measures whether the model
names a track); a debug mode in `scorecard.py` (a new check script, a
step of its own).
Decided (2026-11-18), defaults: a run without a board keeps today's
prompt line ("never name traces or vias"), and a `board` link in it is
rejected as above. Review mode keeps rework empty, board or not.
Decided (2026-11-18), in the build: `Problem` gains `rework`, the index
of the rework step whose board link the evidence check removed; the UI
shows its reason under that step (`reworkBoardLine` in `present.ts`),
not in the hypothesis's problem list, and a confirmed link as "Checked on
the board". The bench report (`toMarkdown`) writes the same line.
Decided (2026-11-18), in the build: the structure prompt is
`STRUCTURE_BOARD` when a board was sent and `STRUCTURE` otherwise
(`prompt.structure_prompt`), and the run records which by name.
`EvidenceContext` gains `board_sent` and the parsed `board`; the record
keeps only `board_sent`, so `make replay` rejects a board link as "the
board's copper isn't available".
Decided (2026-11-18), in the build: `layout.copper_path` gives the
shortest copper path between two pads with the tracks and vias it runs
through, on the copper graph `_paths` already built (now `_graph`).
Decided (2026-11-18), in the build: `usbc-buck-cin-far` keeps the
planted `board.kicad_pcb` (351 KB, the source's MIT licence), so
`app_prompts.py` parses it for the evidence check. Its layout facts are
the ones in `layout.txt`; one dangling track stub the planting script
had pruned from `layout.txt`'s board was pruned here too. Its DRC needs
the source's `.kicad_pro`, which the case doesn't keep.

**Checks:** `tests/test_debug_layout.py` gains 4 `copper_path` tests
(20 in all): a T-junction path of 13.0 mm over 3 tracks, a via to the
back layer (2 tracks, 1 via), a path across a zone (no tracks), and none
across nets or with no copper. `tests/test_debug.py` gains the board
rework tests: a cut, a drilled via and a jumper pass; a cut with no
track, a drilled via with no via, a missing pad, a net not in the
schematic, a pad on another net, no copper between the pads, no board
sent and copper not available are each rejected, the step kept without
its link and the problem carrying its index. `tests/test_debug_service.py`'s
sent-board test checks the `STRUCTURE_BOARD` prompt, the recorded prompt
name and `board_sent` round-tripping without the copper.
`present.test.ts` gains 3 `reworkBoardLine` tests. `make lint` and
`make test` passed (566 backend, 68 frontend), each sub-target run on its
own. `make eval-app case=usbc-buck-cin-far` in debug mode, 3 runs with
the answer cache off: the bug ranked #1 in all 3; 5 rework steps (a
10 µF capacitor tacked at U1, reflowed joints, a higher-current L1,
separate wires from J4 to the loads), none of them a cut, jumper or
drilled via, so none linked to the board and no link was rejected. The
model never had cause to name a track or via on this fault, so only the
tests cover the check. `make eval-score` (`evals/README.md`, "With board reworks") against
the sweep above, review mode, whose prompts didn't change: 57 runs, 0
failed; planted cases ranked the bug #1 in 22 of 33 runs (22 before),
1 miss (`usbc-buck-cin-far` run 2, an Auto-check miss: its #3 issue cites
`pl1`); real bugs 5 #1 and 7 missed (7 and 4 before, run-to-run
variation in `enviro-node-usb-backfeed` and `lpddr5-fb-no-divider`); 53
issues judged wrong (53 before), 19 of 37 on the unmodified boards.
Bugs found: `copper_path` counted two touching track ends (joined at
0 mm) as a track; ruff caught an import out of order in `evidence.py`;
the 2nd and 3rd `make eval-app` runs came back from the AI answer cache
in 0 s, so they were rerun with `AI_CACHE_TTL_S=0` as `scorecard.py`
does; the review found `toMarkdown`'s doc comment left above
`reworkBoardLine`.
