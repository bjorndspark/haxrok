# Plan 004: Read each part's datasheet, check its reference design, simulate with the vendor's model

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-07 | 2026-11-07 | Proposed | — | `001-mvp-scope.md`, `003-simulate-and-ask.md`, `DECISIONS.md` ("Datasheet facts come from memory until retrieval") |

Part facts come from the model's memory today, so they're labelled
"unverified" and can't back a finding on their own. A hand-written pin table
and six hand-copied reference notes (`reference.py`) cover a few parts. And
ngspice only runs RC circuits the backend builds itself (`spice.py`), with
ideal sources standing in for real parts.

The goal: for each part in an uploaded schematic, the app downloads its
datasheet and reads the facts from it, cited by page. It then compares the
board with the datasheet's typical application circuit, and simulates the
circuit with the manufacturer's own SPICE model. When that model is PSpice or
LTspice, the app first converts it so ngspice can run it. Every fact,
deviation and simulation result becomes evidence the review can cite, with
its source.

This reverses `DECISIONS.md` "Datasheet facts come from memory until
retrieval", which rejected retrieval for the hackathon (finding the right
PDF from a value like "LDO", and the extra latency). Step 1 records the
change there.

Decided (2026-11-07): the work happens on `dev`.
Decided (2026-11-07): manufacturer models in PSpice and LTspice syntax are
both converted for ngspice.
Decided (2026-11-07): a part whose model is encrypted, or that has no model,
is left out of the simulation and shown as such ("encrypted model", "no
model"). No model is made up from datasheet numbers.

## 1. Each part's datasheet is found and downloaded: proposed

- `design.parse_netlist` keeps each part's `Datasheet` field and its part-
  number fields (`MPN`, `Manufacturer`, `LCSC`, …), which it drops today.
- A new `datasheet` module resolves a PDF per part: the schematic's own URL
  first, then a lookup by part number. Passives and generic values (`~`,
  `R`, `10k`) are skipped.
- PDFs are fetched once and cached by part number. Each part gets a status
  the UI shows: found (with source), not found, skipped.
- `DECISIONS.md`: the reversal, and what replaced it.

Decided (2026-11-07): without a URL in the schematic, the part number (the
`LCSC` field, else the MPN) is looked up on LCSC, whose product page links
the PDF. No API key; it's an unofficial endpoint, so a change there shows
as "not found", never as an error.
Decided (2026-11-07): PDFs and SPICE models are kept as files under the
git-ignored `data/` (mounted at `/app/data`), keyed by part number, until
deleted. Extracted facts go in Redis like model answers. No database.

**Checks:** unit tests with a faked fetcher (URL field, part-number lookup,
skipped passives, a 404); run on the orbit and rp-micro boards' real
schematics and record how many ICs got a datasheet.

## 2. Datasheet facts are extracted and cited by page: proposed

- The PDF's text and tables are extracted per page in the backend.
- One model call per datasheet (through `app/core/ai/`) turns them into
  structured facts: pin functions, key thresholds and limits, and the
  typical application circuit (parts, values, which pins they join), each
  with its page.
- Cached per part number, so a part is read once, not per run.
- A new evidence kind, `datasheet` (part, page). Pin facts from a datasheet
  take precedence over the hand-written pin table, which stays as a fallback.

Decided (2026-11-07): the PDF text library is pdfplumber (MIT, reads
tables). PyMuPDF is AGPL, so not that.
Decided (2026-11-07): extraction finishes before the review, so the model
can cite datasheet pages. A board's first run is slower (one call per new
IC, in parallel); later runs hit the cache.

**Checks:** golden tests on 3–4 real datasheets (STM32F030, TPS63802,
RP2040, an LDO): extracted pins and thresholds match by hand; the review on
`orbit-boot0-high` cites `datasheet` evidence for BOOT0.

## 3. The board is checked against each datasheet's typical application: proposed

- A pure function compares the extracted application circuit with the
  part's neighbourhood in the netlist: a missing part, a value outside the
  datasheet's range, a pin left open that the datasheet ties.
- Each deviation is a verified issue, like `reference.py`'s notes today,
  citing the datasheet page. The hand-written notes stay where no datasheet
  was read.

**Checks:** unit tests per deviation kind; on `rp-micro-no-fault` review
stays PASS (no false alarms); the L1 0.47 µH vs 2.2 µH mismatch on orbit is
caught against the TPS63802's inductor range.

## 4. Each part's SPICE model is found and classified: proposed

- Looked for next to the datasheet (the manufacturer's product page) and in
  the schematic's `Sim.*` fields. Logins and "request a model" forms are
  skipped.
- Each model is classified: ngspice-ready, PSpice, LTspice, encrypted, or
  none. The UI shows each part's status.

Decided (2026-11-07): models are found automatically from the uploaded
schematic, with no list of supported manufacturers. From the part number,
the search finds the manufacturer's product page (via LCSC's listing or the
datasheet URL's site) and takes links to model files (`.lib`, `.cir`,
`.mod`, `.sub`, `.asc`, `.zip`) or links named SPICE / PSpice / LTspice.
Pages rendered only by JavaScript, logins and bot walls give "no model
found". A site-specific adapter is added only where this fails on a demo
board's part, and each one is recorded here.

**Checks:** unit tests on sample model files of each class; the status of
every IC on the orbit and rp-micro boards recorded here.

## 5. PSpice and LTspice models are converted to run in ngspice: proposed

- First ngspice's own compatibility modes (`ngbehavior=ps` / `lt`).
- What those don't cover is rewritten: `VALUE={…}`, `TABLE`, `LAPLACE`,
  `IF()`, `.FUNC`, parameter syntax. A model that still fails to load is
  shown as "conversion failed", with ngspice's error.

**Checks:** a test corpus of real vendor models (one PSpice, one LTspice per
major vendor) that each loads and runs a DC operating point in ngspice.

## 6. Circuits simulate with real models and their results become findings: proposed

- The circuits around each modelled IC (regulator plus its passives and
  load, a supervisor against its rail, a reset RC) are cut out of the
  netlist and simulated with the vendor's model. A whole MCU isn't
  simulated: it's a load and its pins' thresholds.
- Results are checked against the datasheet's limits (rail tolerance,
  start-up, reset release) and become findings with `simulation` evidence.
  They stream in like plan 003's simulations, and can be asked about.

Decided (2026-11-07): three analyses are built: start-up (rails within
tolerance, their order, reset releasing after the rail is good), load step
(droop and recovery on each regulator's output, stepped to the datasheet's
rated output current) and dropout (input swept down to where the output
leaves regulation). The user picks which to run per run on the upload form;
start-up is ticked by default, load step and dropout are opt-in since they
add the most time.

**Checks:** a planted-fault eval case the simulation catches and a formula
can't (e.g. the ESP32 EN RC against an LDO's soft-start); no-fault cases
stay quiet.

## 7. The UI shows datasheets, models and simulated findings: proposed

- Upload form: which analyses to run (step 6).
- Design summary: per part, datasheet (link, pages used) and model status.
- Findings citing `datasheet` and `simulation` evidence get their labels.
- `README.md` (what the app does), `evals/README.md` (new results),
  `make docs` if routes change.

**Checks:** `make lint`, `make test`; a full run from the UI on orbit.
