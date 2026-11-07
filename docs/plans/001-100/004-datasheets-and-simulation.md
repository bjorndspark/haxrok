# Plan 004: Read each part's datasheet, check its reference design, simulate with the vendor's model

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-07 | 2026-11-07 | In progress | — | `001-mvp-scope.md`, `003-simulate-and-ask.md`, `DECISIONS.md` ("Datasheet facts come from memory until retrieval") |

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

## 1. Each part's datasheet is found and downloaded: done

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

Decided (2026-11-07): only ICs (`U`/`IC` refs) are looked up, in the
order of the summary's IC list, while the run is prepared; the first run
of a board waits for the downloads. Their statuses go in the design
summary (`datasheets`), the kept PDF's path stays on the server.
Decided (2026-11-07): an MPN alone can't be looked up. LCSC's keyword
search is a bot wall, and the app doesn't get past bot walls, so those
parts are "not found" ("no LCSC number to look up"). Only LCSC's product
record by LCSC number is used, with the app's name as User-Agent.
Decided (2026-11-07): the fetcher reaches only public addresses, checked
on each redirect hop, with a size cap (`DATASHEET_MAX_BYTES`) and a
per-request timeout (`DATASHEET_TIMEOUT_S`, ×3 per part). A body counts
as a PDF only if it starts with `%PDF`. Misses aren't kept, so a later
run tries again.

**Checks:** unit tests with a faked fetcher (URL field, part-number lookup,
skipped passives, a 404); run on the orbit and rp-micro boards' real
schematics and record how many ICs got a datasheet.
14 tests in `test_debug_datasheet.py`: the parser's fields, URL first and
the kept file reused with no fetch, LCSC fallback when the URL gives HTML,
not-found reasons (LCSC has no PDF, a 404 URL, MPN only), skipped generic
values, a shared lookup for two of the same part, a part's time budget,
and the fetcher refusing private addresses and a redirect to the LAN, a
404 and an oversized body. Live, from the backend container (8 s, cold):
orbit 5 of 6 ICs found (U17 "~" skipped; STM32F030 from ST's URL, three
from LCSC), rp-micro 3 of 3, rust-board 3 of 6 (U4 skipped; SHTC3's
C194656 is unknown to LCSC; ICM-42670-P has only a manufacturer field).
`make lint` and `make test`: clean (one existing react-refresh warning),
backend 133 passed, frontend 29 passed. Self-review caught that
`/ask-result` would have rejected a summary saved before this change
(`datasheets` was required); it now defaults to empty, with a test.

## 2. Datasheet facts are extracted and cited by page: done

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
Decided (2026-11-07): a datasheet of up to ~120k characters (~30k tokens)
is sent whole; a longer one (RP2040's 642 pages, STM32's 93) is cut to
that budget by page, preferring pages whose headings name pin
descriptions, limits, electrical characteristics, typical application,
boot or reset (and their "continued" pages), and skipping package,
ordering and revision pages. pypdfium2 (pdfplumber's own dependency) reads
every page's text for that choice in under a second; pdfplumber then reads
the chosen pages' tables. Pages are cited by their PDF page number, so
`<url>#page=N` opens them.
Decided (2026-11-07): the model is given the IC's value, footprint and the
board's pin numbers and names, so pin facts use the board's package. A fact
whose page wasn't sent, or whose pin isn't on the board, is dropped.
Decided (2026-11-07): pin facts replace the hand-written table's lines for
that IC and are cited as `pin_function`; limits and application parts get
ids (`ds1`, `ds2`, …) and are cited as a new `datasheet` cite kind. Either
backs `datasheet` evidence. The wiring checks stay on the hand-written
table until step 3.
Decided (2026-11-07): extracted facts are cached in Redis under the part's
key and a hash of the prompt, schema, board pins and model, for
`AI_CACHE_TTL_S`. A failed extraction (no text, scanned PDF, model error)
leaves that IC on the hand-written table and the review goes on.

**Checks:** golden tests on 3–4 real datasheets (STM32F030, TPS63802,
RP2040, an LDO): extracted pins and thresholds match by hand; the review on
`orbit-boot0-high` cites `datasheet` evidence for BOOT0.
20 tests in `test_debug_facts.py` cover the following:
- page choice: the whole sheet when it fits, ranked headings, "continued"
  pages, register tables whose columns say "reset";
- reading: page numbers, a scan, 8 threads reading at once;
- the reader: dropped pages and pins, one read per shared part, a cache hit,
  a corrupt cache entry, failures, a broken PDF, sheets not found;
- pin and prompt merging, and the `datasheet` cite check.

One service test checks that a stream reads the found datasheets and that a
`ds` cite backs a claim.

Live run on 2026-11-07, real model, from the backend container. Neither
board had a failed sheet, and every value below matches the PDF by hand:
- orbit (86 s cold): STM32F030 had 4 watched pins (BOOT0 pin 44, p.33;
  NRST 7, VDD 1, VDDA 9) and 34 facts, including VDD 2.4–3.6 V (p.44),
  BOOT0 max 5.5 V and the NRST 0.1 µF (p.67). TPS63802 had 35 facts: VI
  1.3–5.5 V, L 0.47 µH (p.5), and the typical application's 0.47 µH, 10 µF,
  22 µF and 511k/91k divider (p.17).
- rp-micro (46 s): RP2040 RUN pin 26 (p.12), TESTEN 19, IOVDD absolute
  maximum 3.63 V, 12 MHz crystal and 27 Ω USB resistors. The recommended
  IOVDD range wasn't among its 26 facts. RT9013 had 17 facts: VIN 2.2–5.5 V
  (p.3) and 1 µF in and out (p.2).

The review on `orbit-boot0-high`, with those facts, ranked "BOOT0 strapped
high" #1 [will_fail]. It cited `datasheet` evidence for BOOT0 through the
datasheet-read pin (`pin_function` U16.44). It cited no `ds` id in that run.

`make eval-app case=orbit-boot0-high mode=review`, which runs without
datasheets, still ranks BOOT0 #1 [will_fail].

`make lint` and `make test` are clean (one existing react-refresh warning).
Backend 154 passed, frontend 29 passed.

Bugs found:
- RP2040's register tables (a "Reset" column) outranked its pin pages, so
  only headings score now.
- pdfplumber raised on a page with objects outside the box; it reads with
  `strict=False`.
- The cache key lacked the footprint, so a package change reused another
  package's pins.
- PDFium segfaults on concurrent reads, so `pdf.read` holds a process lock.
  The thread test failed 4 of 4 runs without it.
- The "Reading" type is renamed `DatasheetReading`, and a fact count says
  "1 fact".

Platform issues, not code: Docker's 192.168.0.0/20 pool overlapped the LAN
gateway, fixed in Docker Desktop's address pools. The gateway also gave a
transient 503 `pii_mask_error`, and a rerun passed.

## 3. The board is checked against each datasheet's typical application: proposed

- A pure function compares the extracted application circuit with the
  part's neighbourhood in the netlist: a missing part, a value outside the
  datasheet's range, a pin left open that the datasheet ties.
- Each deviation is a verified issue, like `reference.py`'s notes today,
  citing the datasheet page. The hand-written notes stay where no datasheet
  was read.

Decided (2026-11-07): each application part's allowed range comes from
the extraction. Its schema gives every application part a min, a max and
the page they're on, copied from the datasheet (the model links "CO" to
the output capacitor), so every cached datasheet is read once more. A part
with only a typical value is never flagged for its value.
Decided (2026-11-07): only a missing required part is a deviation. The
extraction also marks each application part optional or required
(optional: the datasheet says so, or it serves a feature the board may
not use, like an LSE crystal or a power-good pull-up). A pull resistor
isn't missing when the board ties that pin straight to a rail.
Decided (2026-11-07): for inductors, an "R as decimal point" code in the
part number or library part (`2R2` = 2.2 µH, `R47` = 0.47 µH) is read
as a value too. It's checked against the range, and a value field that
disagrees with it is reported. Plain 3-digit codes (`100`, `101`) are
ambiguous and ignored, and resistor and capacitor part numbers aren't
decoded.
Decided (2026-11-07): `reference.py`'s notes stay for every part they
cover, whether its datasheet was read or not. They hold app-note and
design-guide facts (the RP2040's intended 1 kΩ crystal resistor) that the
datasheet read doesn't replace.
Decided (2026-11-07), defaults: the check is a pure function in a new
`application.py`, run in the review stream once the datasheets are read.
Deviations are numbered `dv1`, `dv2`, … and listed with the datasheet
facts in the prompt. They're cited with kind `datasheet` and back
`datasheet` evidence. A progress line counts them, and the UI shows them
in step 7. A part is present when a board part of the same kind joins the
nets of its IC pins (with one IC pin, when it's on that pin's net).
Capacitors in parallel are summed. For capacitors only a value below the
minimum is flagged, since datasheets give effective (derated) capacitance
and the nominal value is always above it. Parts of kind `other` aren't
checked.

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
