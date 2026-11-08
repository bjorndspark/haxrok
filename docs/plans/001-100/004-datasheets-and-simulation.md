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

## 3. The board is checked against each datasheet's typical application: done

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
`test_debug_application.py` has 29 tests:
- missing, optional, a pull resistor on a rail vs not, single-pin presence,
  a crystal reaching XOUT through a series resistor, `other` and 3-net
  parts skipped;
- an open pin reported once for two parts;
- the inductor's part-number code vs its value field, out of range,
  typical-only values, a resistor range in kΩ and "kohm", parallel
  capacitors summed, a unit that doesn't fit the kind;
- numbering, the prompt section's place, a `dv` cite backing `datasheet`
  evidence, and parsing henries and R-codes.

`test_debug_facts.py` adds a range read from a page not sent (dropped) and
the fact line's range and "optional". The service stream test checks the
progress count, the prompt's deviation line and a `dv1` cite.

Live run on 2026-11-07, real model, every datasheet read again under the
new schema:
- orbit: 4 sheets read, 134 facts, 2 deviations, both L1 on U15
  (TPS63802): its value field says 0.47 µH but `DFE201612E-2R2M=P2` codes
  2.2 µH, and 2.2 µH is outside the datasheet's inductor range. The review
  ranked "TPS63802 inductor will be fitted 2.2 µH, out of spec" #1
  [will_fail], citing dv1, dv2 and ds31, and BOOT0 #2 [will_fail].
- rp-micro-no-fault: 3 sheets read, 60 facts, no deviations, and the
  review is PASS (no issues).

`make eval-app case=orbit-boot0-high mode=review`, which runs without
datasheets, ranks BOOT0 #1 [will_fail] and the L1 mismatch #2 [may_fail].

`make lint` and `make test` are clean (one existing react-refresh warning).
Backend 185 passed, frontend 29 passed.

Bugs found:
- The first live rp run flagged "no crystal" on the RP2040. Its crystal
  reaches XOUT through R3 (1 kΩ in series), so it wasn't on XOUT's net. A
  part other than a resistor now counts as present through one series
  resistor. The model had already called the deviation a false positive.
- A second required part on an already-open pin was reported as missing
  too. An open pin is now one deviation, however many parts the datasheet
  ties to it.
- `parse_henries` missed "2.2 µH 3A", and 0.47 µH printed as "470 nH".
- pdfminer (under pdfplumber, since step 2) and the SDK's httpx2/httpcore2
  logged at DEBUG and INFO: 11M lines in one session's `app.log`. They're
  quieted to WARNING like httpx.

Platform issues, not code: the gateway's 503 `pii_mask_error` failed two of
rp's datasheet reads and then its review, and held orbit's reads past
curl's 15 minutes; a rerun passed. A backend hot reload while editing also
cut one pair of runs short.

## 4. Each part's SPICE model is found and classified: done

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
Decided (2026-11-07): this step is backend only. The statuses go in the
design summary (`spice_models`, one per IC like `datasheets`); step 7's
per-part panel shows them with the datasheet's.
Decided (2026-11-07): a `Sim.Library` file (KiCad 6's `Spice_Lib_File`)
is used before the web when it's in the upload. The backend accepts SPICE
model files (`.lib`, `.cir`, `.sub`, `.mod`, `.sp`, `.spi`, `.spice`) in
the upload now, never keeps them on disk, and otherwise says "<file> not
uploaded". `Sim.Name` picks the subcircuit. Step 7 makes the browser send
the files `Sim.Library` names, and changes the form's "only .kicad_pro and
.kicad_sch are sent".
Decided (2026-11-07), defaults: the module is `spice_models.py` ("model"
alone means the AI model here). Models are looked up in `prepare` right
after the datasheets, for the same ICs, in parallel, with a time budget
per part. Downloaded model files are kept under `SPICE_MODEL_DIR`
(`data/models/<key>/`, the datasheet's key), classified again on each
read; misses aren't kept. A ZIP keeps only its model text files, with
caps on their count and size. The classifier reads the text: encrypted
(`$CDNENCSTART`, an LTspice encrypted header, binary), then a simulator
named in its comments, then PSpice-only or LTspice-only syntax, else
ngspice-ready. ngspice-ready is a guess from syntax; step 5 loads every
model in ngspice for real. Nothing goes in the prompt until step 6.

**Checks:** unit tests on sample model files of each class; the status of
every IC on the orbit and rp-micro boards recorded here.
Decided (2026-11-07): one site-specific adapter, TI's. LCSC's record
names the brand and MPN but links no manufacturer page, so a TI part
(by LCSC brand or a ti.com datasheet URL) is looked up at
`ti.com/product/<base>`, the orderable suffix trimmed (`TPS63802DLAR`
404s, `TPS63802` doesn't). Its "Unencrypted PSpice Transient Model
Package" link redirects to a zip. The other demo-board vendors gave
nothing to adapt to: Microchip's pages are behind a bot wall (403),
Richtek's and Silergy's link no models, and the MCUs, flash, crystal,
CP2102 and sensors have none. Their status is "no product page known
for <brand>".
Decided (2026-11-07): a status's `source` is `upload` (the file
`Sim.Library` names) or `web` (a product page's link). An uploaded model
isn't kept, so its `path` is ""; step 5 reads it from the upload.

`test_debug_spice_models.py` has 16 tests:
- the classifier on a sample of each class (ngspice, PSpice by comment
  and by syntax, LTspice, Cadence- and LTspice-encrypted, binary), and
  simulator words in code lines ignored;
- the subcircuit pick: `Sim.Name`, then the MPN, then the first subckt;
- ZIPs: basenames only, zip-slip names, member count and size caps; a
  plain download counts only when it reads as a model;
- link ranking on TI's real product page (the unencrypted package first),
  TI product names, and `${KIPRJMOD}`-relative `Sim.Library` paths;
- the lookup: a TI part found by LCSC brand, kept and read back with no
  fetches; the schematic's URL searched as a product page only when it
  gave no PDF; an uploaded `Sim.Library` used before the web and never
  kept; "not uploaded" then the web; generic parts skipped and misses not
  kept; the same part twice looked up once;
- the netlist's `Sim.*` and KiCad 6 field names.

`test_debug_service.py` checks that the stream passes the ICs, their
datasheet statuses and the project root to the model finder, and that
the summary lists the models; and that model files are kept in an upload
and refused without a sheet.

kicad-cli 9's netlist carries `Sim.Library`/`Sim.Name` as fields: tried
by adding both to a copy of rp-micro's sheet. No demo board sets them.

Live run on 2026-11-07, every IC on the demo boards:
- orbit: U15 TPS63802DLAR is `pspice`, `TPS63802_TRANS` in
  `TPS63802_TRANS.LIB` from `ti.com/lit/zip/SLVMCX1`. U1 MCP73031,
  U10 8 MHz oscillator, U16 STM32F030C8Tx and U22 CP2102-GMR are `none`
  (no product page known for the brand). U17 "~" is skipped.
- rp-micro: U1 RP2040, U2 W25Q64JVXGIQ and U3 RT9013-33GB are `none`.
- rust-board: U1 SHTC3, U3 ESP32-C3-MINI-1, U5 SY8088 and U6 MCP73831
  are `none`; U2 ICM-42670-P has no part number or datasheet URL; U4
  "Rust_Board" is skipped.
A second orbit run read the TPS63802 from `data/models/` (2 s).

`make lint` and `make test` are clean (one existing react-refresh
warning). Backend 203 passed, frontend 29 passed.

Bugs found:
- The link ranking test expected TI's tool page above the part's
  encrypted model; the code was right (a model of this part outranks a
  page without one), so the test now asserts the full order.
- Requiring a `.kicad_sch` once model files were accepted changed the
  message for a `.kicad_pro`-only upload. Any KiCad file passes that
  check again, as before.
- A test line over ruff's 100 columns.

## 5. PSpice and LTspice models are converted to run in ngspice: done

- First ngspice's own compatibility modes (`ngbehavior=ps` / `lt`).
- What those don't cover is rewritten: `VALUE={…}`, `TABLE`, `LAPLACE`,
  `IF()`, `.FUNC`, parameter syntax. A model that still fails to load is
  shown as "conversion failed", with ngspice's error.

**Checks:** a test corpus of real vendor models (one PSpice, one LTspice per
major vendor) that each loads and runs a DC operating point in ngspice.

Decided (2026-11-07): vendor models aren't committed (their licences
don't clearly allow redistribution). The corpus is a committed list of
vendor model URLs in a check script, `backend/scripts/spice_corpus.py`
(`make spice-corpus`), that downloads them into `data/spice-corpus/`,
converts and loads each, and prints a table; its results are recorded
here. `make test` runs small hand-written snippets of each construct in
real ngspice (skipped where ngspice isn't installed).
Decided (2026-11-07): the converter starts from ngspice's own modes
(`ngbehavior=ps` / `lt`, set in a `.spiceinit` in the work dir; PSpice
mode already reads `IF()`, `PARAMS:` and `VALUE={}`, tried in the
container). A rewrite is added only for a construct a real corpus model
fails on, with a test and a line here. No speculative rewriters.
Decided (2026-11-07), defaults: the module is `spice_convert.py`;
ngspice stays behind `spice.py`'s `Ngspice` (a new method for a load
check). Every found model (`ngspice`, `pspice`, `ltspice`) is
load-checked on each run in `prepare`, right after the finder, a few at
once. The deck instantiates the picked subcircuit with each pin to
ground through 1 MΩ and runs `.op`, with every file of the model's
folder (or the upload's) beside it. `SpiceModelStatus.status` stays the
dialect; a new `loads: bool | None` says whether ngspice loaded it
(None: nothing to load, or ngspice missing), and a failure's ngspice
error goes in `reason`. An operating point that doesn't converge with
the pins grounded still counts as loaded, noted in `reason`. Converted
text isn't kept: conversion is deterministic, and step 6 converts again
from `path` or the upload.
Decided (2026-11-07): only TI serves models to a plain HTTP client from
here (ADI and ST reject non-browser clients; Microchip, onsemi, Nexperia,
Diodes and Toshiba answer 403; ROHM downloads go through a JavaScript
licence dialog), and bot walls aren't worked around. The corpus is a
list of TI parts run through the app's own finder (step 4) with each
part's TI datasheet URL, as on a real run, kept in
`data/spice-corpus/web/`; the script also loads any model files put by
hand in `data/spice-corpus/manual/` (downloaded in a browser), which is
how LTspice and the other vendors get covered.
Decided (2026-11-07): no rewrite was needed. ngspice 44.2's modes load
every corpus model. (Step 6 found two constructs that load but compute
the wrong value, and rewrites them.) A parameter the model leaves to its schematic (TI's
TPS7A02 divides by `V_out`, set on the schematic symbol) isn't a
conversion failure: when ngspice names an undefined parameter the model
uses as a value, the check sets it to 1 and runs again (up to three),
and `reason` says "needs V_out set by the schematic". Step 6 sets it from
the circuit.
Decided (2026-11-07): a model is untrusted text. One with a
`.control`/`.endc` line (ngspice runs its commands, `shell` included) or
an include path that is absolute, starts with `~`, has a drive letter or
climbs out with `..` is refused with a reason, not run. Only the picked
file and the files it includes, transitively, are checked and put beside
the deck. Upload paths outside the project are left out.

**Checks.** `make spice-corpus` on 2026-11-07, ngspice 44.2 in the
backend container: 8 of 8 checked models load.

| Model | Dialect | Subckt | Loads | Note |
|---|---|---|---|---|
| TPS63802 | pspice | TPS63802_TRANS | yes |  |
| TPS62840 | pspice | TPS62840_TRANS | yes |  |
| TPS54202 | encrypted | TPS54202_TRANS | – |  |
| TLV755P | pspice | TLV75507P_TRANS | yes |  |
| TPS7A02 | pspice | TPS7A02_03 | yes | needs v_out set by the schematic (1 here) |
| TLV9001 | pspice | TLV9002 | yes | loads; no operating point with the pins grounded |
| OPA333 | pspice | OPAx333 | yes | loads; no operating point with the pins grounded |
| TLV3201 | pspice | TLV3201 | yes |  |
| INA180 | pspice | INA180A1 | yes |  |

No LTspice model is in it yet: none is reachable without a browser. Put
some in `manual/` and rerun.

`test_debug_spice_convert.py` has 37 tests (19 functions, parametrized).
- The deck, `.spiceinit`, subcircuit pins with `+` lines and `PARAMS:`.
- The refusals: `.control`, absolute, `~`, drive and `..` includes, and a
  deck name with a quote or control character.
- A vendor test bench that includes `..\SCHEMATIC1.net` is ignored.
- Verdicts on canned logs, the same model checked once, a `.model` card
  skipped, ngspice missing, and a schematic parameter set and noted.
- `Ngspice.load` with fake ngspice scripts: the timeout, and a file name
  outside the work dir.
- Real ngspice: 11 constructs that load in ps/lt mode (`VALUE`,
  `PARAMS:`, `IF()`, `TABLE`, `STP()`, G `VALUE`, `VSWITCH`, `uplim`,
  LTspice `params:`, `SW`, LTspice diode `Vfwd`/`Ron`), and three that don't (PSpice
  `LAPLACE`, LTspice `Rser=` on C/L, LTspice `if()`). A rewrite for those
  waits for a real model that needs it. A `.control` canary isn't run.
`test_debug_service.py` checks that `prepare` load-checks each found
model.

`make lint` and `make test` are clean (one existing react-refresh
warning). Backend 241 passed, frontend 29 passed.

Bugs found:
- TI's model ZIPs hold test benches that include `..\SCHEMATIC1.net` and
  `../../../tlv3201.lib`, so the include check refused TLV9001 and
  TLV3201 at first. Only the picked file and what it includes are
  checked now.
- Without a datasheet URL the finder names TI's product page from the
  MPN's stem, which gives `TLV755` for TLV755P and `TPS7` for TPS7A02:
  no model. The corpus passes the datasheet URL, as a real run does.
  Later: a better stem for names like TPS7A02 in step 4's finder.
- TPS7A02 failed with "Undefined parameter [v_out]" until the
  schematic-parameter retry.
- With a failed op, ngspice fell back to its transient op, over 20 s on
  TPS63802. `.spiceinit` now sets a 2 ns `optran`.
- Lint: a docstring line over 100 columns, an unsorted import, and a
  `Log(...)` call as a default argument (B008). The diff review added
  `Ngspice.load`'s own docstring.

## 6. Circuits simulate with real models and their results become findings: in progress

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

Tried on 2026-11-07: TI's TPS63802 model, as orbit wires it (VIN 3.6 V,
0.47 µH, 44 µF, 560k/100k, 3.3 Ω), simulates 1.5 ms of start-up in about
150 s in the backend container (ngspice 44.2, PSpice mode, only `v(vout)`
and `v(pg)` saved; saving every node ran out of memory). It overshot to
4.3 V and then sat at 3.50 V with PG high, in pass-through: ngspice
misread two PSpice constructs in the model (2026-11-08, below). Only this IC has a model on the demo boards (step 4),
so the ESP32-EN-against-soft-start example can't be simulated.

Decided (2026-11-07): simulations run alongside the review, as plan 003's
do. The backend checks each waveform against the datasheet limits and
streams a verdict (passed or flagged), a finding it makes itself with
`simulation` evidence, like a flagged `Calculation`. The model's review
doesn't see or cite them, so no run waits on them before the review. Ask
works on them as on plan 003's simulations.
Decided (2026-11-07): in start-up, the regulator drives a resistor that
draws the datasheet's rated output current at the nominal output, the
worst case, and the same current the load step steps to. The ICs on the
rail are replaced by it. Without a rated current in the datasheet facts,
the regulator isn't simulated and its status says why.
Decided (2026-11-07): the eval case is `orbit-vout-cap-small`: orbit's
C13 (22 µF on +3.3V) shrunk to 2.2 µF, so the start-up overshoot and the
load-step droop leave the STM32's VDD range. The netlist formulas only
check that a cap is there. The no-fault orbit cases stay quiet.
Decided (2026-11-07): one vendor-model simulation is stopped after 4
minutes (15 since 2026-11-08, below) and shown as "timed out". The
result still waits for the simulations, at most about 2 minutes past the
review (no longer: 2026-11-08, below). Windows are sized
from the datasheet's start-up time; only the nodes a verdict reads are
saved.
Decided (2026-11-07), defaults:
- the module is `spice_circuits.py` (the cut-out, decks and verdicts);
  ngspice stays behind `spice.py`'s `Ngspice`, the model text comes from
  `spice_convert` (converted again from `path` or the upload, with its
  `.spiceinit`), and the include and `.control` checks apply as in step 5.
- The cut-out is the IC's pin nets with the passives (R, C, L, D) that
  touch them; rails and ground end it. Other ICs on the output are the
  load. A reset or enable RC on the output to an MCU pin is kept, and its
  release is checked against the rail reaching tolerance.
- The input is a source ramped up at power-on. It takes the voltage the
  input net's name gives (`+5V`, `VBUS` → 5 V) or else the datasheet's
  typical input. Without either, the regulator isn't simulated.
- The limits are the regulator's output accuracy from its facts (±5 %
  without one) and the downstream ICs' maximum supply voltage from their
  facts (nominal +10 % without one). The start-up time is the datasheet's.
- Only models with `loads` true are simulated. Two vendor-model
  simulations run at once. The SSE `simulation` event carries them,
  with a verdict (`passed`, `flagged`, `timed_out`, `failed`) and the
  limits as reference lines.
- The analyses a run does come from a new upload form field, `analyses`
  (`startup`, `load_step`, `dropout`; `startup` when absent). Step 7 adds
  the checkboxes. Dropout runs only for regulators whose datasheet gives
  a dropout voltage; a buck-boost like the TPS63802 has none.
- This step is big (it adds an SSE payload and a form field; step 7 draws
  them), so it's built in its own session.
Decided (2026-11-08): the datasheet facts prompt (`DATASHEET_FACTS`) also
asks for a regulator's rated output current, output voltage accuracy and
dropout voltage. It asked for neither, and TI's TPS63802 facts had no
rated current, so orbit's only modelled IC would have been skipped. The
prompt is in the cache key, so each datasheet is read again once.
Decided (2026-11-08): the datasheet's typical input is an input-voltage
limit's `typ`, else the most common `VIN = x V` in the facts' conditions
(3.6 V for the TPS63802).
Decided (2026-11-08): `spice_convert` rewrites two PSpice constructs
ngspice 44.2 gets wrong, in every PSpice-classed file before a run:
`LIMIT(x, a, b)`, which evaluates to 0, becomes
`min(max(x, min(a, b)), max(a, b))`; and `^`, PSpice's XOR, which ngspice
reads as a power, becomes `!=` on truth values (`&` binds tighter and `|`
looser, as in C). Only `{…}` expressions on non-comment lines are touched,
with `+` continuations joined. A top-level deck can't test them: ps mode
reads only included files.

Built on 2026-11-08, first part (the rewrites); the simulation build
(`spice_circuits.py`, the SSE payload, the form field) is left for the
next session. Found on the TPS63802 model:
- `LIMIT` evaluated to 0, and `^` broke its XNOR gates (a=1, b=0 gave 1;
  a=b=1 gave 0). The off-timer's two DFFs (U9/U12) count through an
  XNOR, so the counter never advanced and the converter deadlocked in
  pass-through: the 3.50 V of 2026-11-07.
- With both rewritten, it regulates to 3.30 V (3.6 V in, DC), then
  ngspice aborts with "timestep too small" at the zero-delay loops of the
  model's `one_shot` subckt (`x_u_gmi_u831.ch`, `x_u_offtimer_u859.ch`)
  and `u_offtimer_buck_50ns_off`. How far it gets depends on the max
  step: 5 ns reaches 401 µs in 137 s, 2 ns reaches 808 µs in 539 s, 20 ns
  and 100 ns fail near 300 µs. A ramped input fails the same way.
- No solver option moved it (`method=gear`, `reltol`, `abstol`, `vntol`,
  `itl4`, `maxord`, `gmin`, `rshunt`). A 1 Ω / 10 pF RC on the one_shot's
  `ch` and `out` made every run hang past 600 s. The one_shot alone
  simulates fine.

Built on 2026-11-08, second part (the loops); the simulation build
(`spice_circuits.py`, the SSE payload, the form field, the service
wiring) is still left for the next session. Found:
- `spice_convert.break_loops` delays each zero-delay loop of
  `E … VALUE` sources, in every PSpice-classed file after the rewrites:
  one source per loop drives `<node>_hxd`, and a 1 Ω / 100 pF RC carries
  it to the node. It cuts the source that drives a subckt pin, else the
  first in the file. In the one_shot that's `e_abm3` on `out`, the only
  hand-made cut that worked (RCs on both `ch` and `out`, above, hung).
  Of the corpus's models, the TPS63802 and TPS62840 have such a loop,
  both in their one_shot.
- With it, orbit's TPS63802 start-up runs through without "timestep too
  small" (3.6 V in, ramped over 50 µs; 0.47 µH, 44 µF, 560k/100k,
  3.3 Ω): switching starts near 330 µs, VOUT reaches 3.30 V by 450 µs
  and stays within 3.299–3.304 V to 700 µs, PG goes high by 500 µs, and
  the peak is 3.56 V.
- It's slow. Before switching (the first ~320 µs) costs ~25 s; switching
  runs at ~0.75 µs of circuit time per second. The 360 µs window took
  60 s without a max step and 62 s with a 5 ns one plus `.options klu`,
  against 79 s before (two at once). The full ~750 µs start-up, two at
  once, didn't finish in 600 s with `.options klu` (with or without
  `reltol=0.003`). With a 900 s cap, `.options klu reltol=0.003`
  finished in 710 s, while the default `reltol` beside it still hadn't.
  So the decks use both options, and a window ending soon after the rail
  settles (about 600 µs here) saves more. TI ships no averaged model
  for either part, so the 4-minute timeout can't cover a switching
  start-up.

Decided (2026-11-08), replacing the 4-minute timeout of 2026-11-07: a
vendor-model simulation is stopped after 15 minutes. The review's
`result` is sent when the review ends (with plan 003's quick
simulations), and vendor-model `simulation` events follow it on the same
stream, which closes after the last one; the frontend already appends
simulations that arrive after the result. A run with orbit's TPS63802
takes about 10–12 minutes in total. Step 7 shows that simulations are
still running after the review.

**Checks:** a planted-fault eval case the simulation catches and a formula
can't (`orbit-vout-cap-small`, above); no-fault cases stay quiet.
First part (2026-11-08): `make test-backend` 249 passed, including real
ngspice runs of a PSpice-mode XNOR (full truth table) and LIMIT clamp
through the rewrite; ruff, ruff format, mypy, eslint, tsc and prettier
clean (one existing eslint warning in `shared/ui/button.tsx`). No
frontend change, so `make test-frontend` wasn't run.
Second part (2026-11-08): `make test-backend` 252 passed, including
the one_shot loop's exact cut, every loop in a file cut with chains left
alone, and loops broken in PSpice files only; ruff, ruff format and mypy
clean. No frontend change. The start-up timings above are from runs in
the backend container, two at once.

## 7. The UI shows datasheets, models and simulated findings: proposed

- Upload form: which analyses to run (step 6).
- Design summary: per part, datasheet (link, pages used) and model status.
- Findings citing `datasheet` and `simulation` evidence get their labels.
- `README.md` (what the app does), `evals/README.md` (new results),
  `make docs` if routes change.

**Checks:** `make lint`, `make test`; a full run from the UI on orbit.
