# Plan 013: PCB checks

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-17 | 2026-11-17 | proposed | — | `001-mvp-scope.md` (sends only `.kicad_pro` and `*.kicad_sch`; "the PCB stays here"), `012-benchmark-scorecard.md` (checked a board's pad nets against its `.kicad_pcb` by hand), `DECISIONS.md` ("No auth": runs are recorded on disk) |

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
| 1 | An opt-in PCB upload runs KiCad's DRC with schematic parity, and its findings are listed next to ERC's | proposed |
| 2 | The model reads the DRC findings and computed layout facts, and cites them | proposed |
| 3 | Rework can name tracks and vias | proposed |

## 1. An opt-in PCB upload runs KiCad's DRC with schematic parity, and its findings are listed next to ERC's: proposed

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

**Checks:** a test for the DRC report parser on a recorded KiCad 9
report; a test that a project without a board runs as today; a board with
a deliberate parity error (a footprint deleted) shows that finding in the
UI.

## 2. The model reads the DRC findings and computed layout facts, and cites them: proposed

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

## 3. Rework can name tracks and vias: proposed

- `Rework` allows a track cut, a jumper wire between two pads, and a via
  to drill out, each tied to a track or pad the board has.
- The evidence check rejects a track or via the board doesn't have.

**Checks:** schema and evidence tests; `make eval-score` against the
baseline.
