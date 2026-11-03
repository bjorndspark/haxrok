# Plan 001: MVP scope for the hackathon

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-01 | 2026-11-03 | MVP implemented; stretch open | — | `evals/README.md`, `README.md` |

What Häxrök builds for the Meta Global AI Developer Hackathon (2026), what
it leaves out, and the other ideas that were weighed against it. The
constraint is a small scope that must use the Meta model, so the MVP is the
smallest thing that demos the real bug end to end.

## Who it's for

Hobbyists who design their own boards in KiCad, have a board on the bench
that misbehaves, and own a multimeter but often no scope. This drives the
prompt as much as the UI:

- **The mechanism is explained in plain words by default.** Nets and
  pins are still shown, as a detail line under the explanation.
- **Confirm steps assume a multimeter.** A step that needs a scope is
  marked as such and gets a multimeter alternative where one exists.
  Each step says what reading means what ("~0 Ω means shorted, which
  confirms this").
- **Fix steps describe real rework** (cut this trace, bodge-wire that
  pin) as well as the schematic fix for the next revision.
- **Jargon gets a hover definition** (ERC, buck-boost, back-feed).
- **The symptom box is one textarea whose placeholder asks concrete
  questions**: what powers the board, what works, what doesn't, what you
  measured, whether anything gets hot.
- **Evidence labels use plain names** and say what a claim rests on, not
  that it was verified: "Based on your schematic", "Based on KiCad's
  checker", "Based on the datasheet pin table", "Based on your
  measurement", "No direct evidence".

Pro tools such as Flux aim at engineers. A debugger that explains *why* is
also the clearer pitch.

## Go/no-go result

`evals/gonogo.py` on a real bug from one of our own boards, model
`meta/muse-spark-1.3-contributor`. The board is private, so its case isn't
in the repo:

| Condition | Rank of the real bug | Time | Tokens (reasoning) |
|---|---|---|---|
| netlist | #1 | 95 s | 16.2k (9.1k) |
| netlist+erc | #1 | 130 s | 22.6k (12.0k) |

By the table in `evals/README.md`, finding it from the netlist alone is a
**go**. The reasoning was real, not keyword matching: it traced why the
board works on some supplies and fails on others through the circuit.

Weaknesses seen, which the MVP has to address:

- **Invented evidence.** It labelled a DMM measurement CONFIRMED, but no
  measurement was in the input.
- **Hallucinated datasheet facts.** It invented a pin requirement for one
  of the ICs and recommended rework based on that.
- **The deeper mechanism was missing.** It never explained why the
  regulator didn't current-limit.
- **Hand-waved gaps.** One failing supply was explained by "source sag"
  instead of being marked as an open question.

This is one case, run once. That's enough to start building, not to claim
accuracy. See the next steps below.

### Planted-fault cases (2026-11-02)

Five faults planted in three open-source boards, plus one unmodified board
whose real cause is a charge-only USB cable. Per-case results are in
`evals/README.md`. All five planted causes ranked #1 in both conditions.
Planted faults are cleaner than real bugs, so this shows the reasoning
carries across boards and fault kinds, not accuracy on real boards.

New weakness:

- **Invented design faults.** On the fault-free board, both runs put the
  cable first but then called a correct part (the 1k series resistor on
  the crystal, as in the Pico reference design) a design error and told
  the user to rework it. Hallucinated part facts showed up in 3 of 12
  answers. See `DECISIONS.md`, "Datasheet facts come from memory until
  retrieval".

## MVP (in scope)

One feature, `debug`, named the same on both sides
(`backend/app/features/debug/`, `frontend/src/features/debug/`).

1. **Input.** Drop the KiCad project folder onto the page (or pick it
   with a folder button), and type the bench symptoms. The user doesn't
   export anything by hand.
   - The frontend sends only the `.kicad_pro` and `*.kicad_sch` files,
     so the PCB, backups and 3D models stay on the user's machine. A
     single `.kicad_sch` also works for a flat, one-sheet project.
   - The backend finds the root sheet (the one named after the
     `.kicad_pro`) and checks that every sub-sheet it references was
     sent. If one is missing, it names the file instead of running.
   - It runs `kicad-cli sch export netlist` and `kicad-cli sch erc`,
     with a timeout, and drops ERC warnings caused only by missing
     symbol libraries. The schematics embed their symbols, so the
     libraries aren't needed.
   - It compacts the netlist with the `evals/compact_netlist.py` logic
     (moved into the feature).
   - The backend image installs Debian's `kicad` package without
     recommends (no libraries). Its `kicad-cli` must be at least as new
     as the user's KiCad, because older versions can't open newer
     files.
2. **One model call** (built as two: diagnose in free text, then
   structure it; see `DECISIONS.md`, "Debug analysis is two model
   calls"). It starts from the `gonogo.py` prompt, reworded for
   hobbyists (see "Who it's for"), through `app/core/ai/` with
   `AI_CHAT_MODEL`. It returns structured JSON, not markdown, so the UI
   can render it and the backend can check it. It carries the
   `gonogo.py` rule on recalled part facts: they are unverified, and an
   unverified fact alone never justifies calling the design wrong or
   recommending rework.
3. **Pin-function lookup.** For each IC on the board it gives the model
   the real pin names and functions, so it stops inventing them. This is
   the fix for the biggest weakness, so it's in the MVP. The MVP source is
   a hand-written table for a handful of ICs (`pins.py`). Datasheet
   extraction comes later. The board summary
   shows which ICs have pin data and which don't.
4. **Output.** Ranked hypotheses showing the nets and pins involved, how to
   confirm, and the fix, followed by open questions the input can't
   answer. Every claim is one fact with an evidence label: schematic, ERC,
   datasheet (the pin table), measurement (a value the user entered) or
   none. The backend resolves every citation against the parsed input and
   drops a claim to none when one fails or when the text names a pin or
   net it doesn't cite. See `DECISIONS.md`, "Evidence labels are checked
   references, not verified conclusions". The contract is
   `backend/app/features/debug/schemas.py`, and the checker is
   `evidence.py`.
   - Confirm steps say whether they test the cause or only check that the
     board matches the schematic. A step that changes the board lists the
     full intended rewiring (`setup`) and what each reading establishes.
   - Rework stays at pin and part level, since there's no PCB data.
   - A run takes 4–6 minutes (two calls of ~90–150 s), and the model
     streams nothing for the first ~90 s of each call (no reasoning text
     comes through the gateway). So the SSE stream sends the design
     summary at once, then our own stage updates, then the result. The
     UI shows elapsed time. Cards don't arrive one by one. The debug call
     needs its own timeout, longer than `AI_TIMEOUT_S` (120 s per read),
     because a go/no-go run took 130 s.
   - Symptoms are numbered by line, so each line has to stand on its own
     ("Fails on battery", not "battery"). A test run whose lines lacked
     that context couldn't tell which supplies passed. The UI nudges for
     this rather than splitting the input into fields.
5. **Add a measurement and re-run** (once 1–4 work). Under each confirm
   step the user can enter what they measured. It's added to the input
   as a bench source and the analysis runs again, with the earlier runs
   kept on the page. This is one more call with more input, not the
   agent loop.

Done means: a real bug's KiCad project runs from the UI and ranks the
fault #1. Met on 2026-11-03 on a private board, so no demo board ships
with the app.

## Stretch (only after the MVP works)

In priority order. If time runs short, cut from the bottom, and keep the
source labels over the simulation.

1. **ngspice check of the top hypothesis**, with a waveform shown in the UI.
2. **Agent loop.** The model asks for a pin lookup or a sim instead of
   getting everything up front.
3. **Pre-fab review mode.** The same pipeline with no symptoms: "review
   before you order". This is the larger market (every board, not only
   broken ones), and the real bug was visible in ERC before fab.
4. **Basic-part substitutes.** When a part is out of stock or is an
   extended JLCPCB part, suggest a basic part that fits the circuit.

## Out of scope

- Importers for non-KiCad tools (Altium, OrCAD). The model only sees the
  compact netlist and ERC text, so keep KiCad parsing behind one import
  step. A later importer is then an addition, not a rewrite.
- A KiCad plugin front end.
- Layout, DRC, assembly or firmware faults. The input is a schematic
  netlist, so the claim is "schematic causes of bench symptoms" only.
- Auth, accounts, history. See `DECISIONS.md`, "No auth".

## Ideas considered instead of Häxrök

| Idea | Need | Fit for the Meta model | Why not now |
|---|---|---|---|
| JLCPCB BOM check: availability, price estimate | Real | Weak: lookups and arithmetic | `kicad-jlcpcb-tools` and JLCPCB's own BOM upload already cover most of it. There's no open JLCPCB parts or pricing API, so it means scraping or a community dump, and assembly pricing isn't a published formula. The one model-shaped part, substitutes, is kept as stretch 4. |
| Datasheet → symbol + footprint | Very high | Good for pin tables, weak for footprint geometry | SnapEDA and Ultra Librarian cover popular parts. One wrong pinout is a dead board, so every output needs a checking step. |
| Pre-fab AI design review | High | Good, same skill | Crowded (Flux and others). Kept as stretch 3 on the same engine instead. |
| AI autorouting | Real | Poor: geometry and optimisation | Funded startups already work on it, and it doesn't fit a hackathon. |
| Visual schematic diff | Real | Barely uses a model | KiRi and similar tools exist, and it doesn't showcase the Meta model. |

### JLCPCB BOM check (parked for later)

Notes so this can be picked up without redoing the research. These are
from memory, not freshly checked, so verify before building.

- **What it would do:** take the KiCad BOM and check JLCPCB/LCSC stock,
  basic vs extended parts and a price estimate for fab plus assembly.
- **What already exists:** `kicad-jlcpcb-tools`, a KiCad plugin that
  matches LCSC part numbers, shows stock and the basic/extended split, and
  generates the BOM and placement files. It's built on `jlcparts`, a
  community-built parts database. JLCPCB's own BOM upload also checks
  parts and quotes a price at order time.
- **Data:** no known open JLCPCB parts or pricing API, so it means the
  `jlcparts` dump or scraping. Assembly pricing (setup fees, per-type
  charges for extended parts, board options) isn't a published formula and
  changes, so a price estimate would drift.
- **Where the model actually helps:** suggesting a substitute that fits
  the circuit when a part is out of stock, or swapping extended parts for
  basic ones to cut the per-type fee. This uses the netlist, so it builds
  on Häxrök's parsing and pin lookup rather than being a separate product.
  That's why it's stretch 4 rather than its own project.
- **Revisit if:** JLCPCB opens a parts or pricing API, or users of the
  debug feature ask for a "can I build this board" check.

Why Häxrök: the core pipeline already works (`gonogo.py`), it's proven on
a real bug from our own board, and starting from bench symptoms sets it
apart from linters that run before fab.

## Product risks

- **Trust.** One confident wrong claim and engineers stop believing it.
  The evidence labels and pin lookup are the mitigation, which is why both
  are in the MVP. The labels only prove the cited facts exist; a wrong
  conclusion drawn from true facts still gets through.
- **A narrow slice.** Many bring-up faults aren't in the schematic.
  Position it accordingly.
- **Thin evidence.** One real case (private, not in the repo) and six
  planted ones, each run once.
  Planted cases aren't accuracy on real boards; don't pitch them as that.
- **Market after the hackathon.** KiCad users spend less, so the paying
  segment is likely small hardware teams and consultancies.

## Next steps

1. Rerun `gonogo.py` 2–3 times to check the ranking is stable.
2. Add a real-bug case to the repo. Public KiCad repos with fix commits are a
   source of labelled cases. (The fault-free board is done:
   `rp-micro-no-fault`.)
3. ~~Build the MVP, then replace the template's seed chat, voice and image
   features.~~ Done 2026-11-03.

## After the hackathon

- **Per-part datasheet retrieval.** For the ICs the top hypotheses rest
  on: part number → datasheet PDF → pin table, key specs and reference
  circuit, cited by page, with a drop-in PDF when lookup fails. It
  replaces the hand-written pin table. See `DECISIONS.md`, "Datasheet
  facts come from memory until retrieval".
