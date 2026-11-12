# Decisions

The few trade-offs worth recording. Append at the bottom; don't fork docs.

## AI is delegated to an external gateway

**Decision:** the app talks to one OpenAI-compatible endpoint (`AI_BASE_URL`
+ `AI_API_KEY`, model names as env vars). All provider concerns — keys,
routing, fallbacks, budgets, observability — live behind that endpoint:
Meta's API directly by default, or a gateway such as Bifrost or LiteLLM when
routing or fallbacks are wanted. `app/core/ai/` is the entire AI surface: one `AsyncOpenAI` client
plus thin call helpers (`chat.py`, `structured.py`).

**Alternatives rejected:** an in-repo provider abstraction + model catalog
(what Fulcrum built — ~120 files, a second product); embedding a gateway as
a compose service (the repo shouldn't manage infra it doesn't own).

**Consequence:** switching providers/models is gateway config, not a code
change. Features that need provider-specific behavior have nowhere to put it
— that's the point. One narrow exception: `AI_CHAT_FALLBACKS` forwards a
`provider/model` fallback list verbatim as the gateway's `fallbacks` request
field (via `fallbacks_extra_body` in `app/core/ai/client.py`). It's a
pass-through, not routing logic — unset, the gateway's own VK/routing
fallbacks apply; set, it overrides them.

Cost protection is the endpoint's job too: with no auth (below), a public
deploy lets anyone start debug analyses (two long model calls each), so
the budget and rate limits on the key in `AI_API_KEY` (the Meta API key,
or a gateway virtual key) are the spend ceiling. The app adds no rate limiting of its own — one place to
set limits, and it's the place that sees every provider's price. Configure
them before exposing a deploy.

## No auth

**Decision:** the API is open. No users table, no tokens, no middleware.

**Consequence:** per-user anything is out of scope until a real need exists.
If auth is added later, add it as a feature package — the structure has an
obvious home for it. Until then a deployed instance is open to anyone who
finds the URL; AI spend is capped at the gateway (see above), and the
unauthenticated log ingest and debug upload endpoints are bounded only by
their request-size limits (`DEBUG_MAX_FILES`, `DEBUG_MAX_UPLOAD_BYTES`).
Every analysis run is recorded on disk (see "Runs are recorded as
files"), so a public instance keeps everyone's symptoms and netlists
until someone deletes them.

Auth belongs in derivative projects, not the template — many don't need
it, and the first real one defines the requirements. The intended shape:
an `auth` feature delegating identity to a hosted OIDC provider (free
tier; self-hosted Zitadel/Authentik as the swap-in), opt-in via config the
way `AI_BASE_URL` is, with each recorded run (`data/runs/`) gaining an
owner.

## Conventions over enforcement

**Decision:** architecture rules live in `AGENTS.md` as conventions; tooling
catches bugs (ruff, mypy, eslint, tsc, pytest) and pins formatting (ruff
format, prettier), but nothing enforces boundaries in CI.

**Alternatives rejected:** import-linter / eslint-boundaries contracts (the
flat structure leaves little to enforce — "features don't import each other"
is one sentence, not a contract file).

**Consequence:** drift is caught in review, not by the build. If the codebase
grows past ~10 feature packages, revisit — retrofitting one contract is cheap
when the structure is clean.

## No database; Redis is shared infra

**Decision:** the app keeps no database. A debug run takes an upload and
streams back its result; what outlives the request is files (datasheets,
models, each run's record, see "Runs are recorded as files"), so there is
no database: no
Postgres, no SQLAlchemy, no migrations. The only stateful service is Redis,
which caches gateway answers. It lives in `docker-compose.infra.yml`
(`name: infra`, one stack per machine whichever copy starts it); the
backend reaches it by service name on the external `infra_default` network.

**Alternatives rejected:** keeping the template's Postgres + Alembic with an
empty schema. It would be scaffolding with no tables behind it, and a
migration chain whose only steps created the template's chat tables and then
dropped them.

**Consequence:** `docker compose up` alone fails without the infra stack —
`make dev` is the entry point, not the compose file. A feature that needs to
persist data brings the database back: add the dependencies, an `app/core/`
engine, Alembic, and a CI job that applies migrations to a real Postgres.

## Envelope responses

**Decision:** every JSON endpoint returns the envelope in
`app/core/responses.py`: `{success: true, data}` on success
(`PaginatedResponse` adds `pagination`), `{success: false,
error: {code, message}, request_id}` on error. Non-JSON responses (SSE,
file mounts) are exempt.

**Alternatives rejected:** bare-model success responses with errors only in
`{"error": ...}` — the original choice, reversed so clients get a single
parse path and pagination has a canonical home.

**Consequence:** routers return `ok(payload)`; the frontend unwraps in
`shared/api/client.ts` (`apiGet`/`apiPost`/`apiDelete`, `ApiError`).
Deletes and fire-and-forget writes (`POST /logs/frontend`) return 200 +
`{"success": true, "data": null}` — 204 can't carry the envelope.

## Checks run in parallel; `uv run --no-sync`

**Decision:** `make test` and `make lint` fan out into per-check targets
(`test-backend`, `lint-mypy`, …) via a recursive `make -j` — wall time is
the slowest single check, not the sum (~6s → ~2.5s). Backend check targets
use `uv run --no-sync`, skipping uv's per-invocation lockfile check and
bytecode recompile (~0.4s each).

**Alternatives rejected:** plain `uv run` — its sync guarantee is real but
redundant: every sanctioned dep path already syncs the venv
(`docker-entrypoint.sh` on container start, `make install`,
`scripts/docker/uv.sh`).

**Consequence:** if `uv.lock` is edited out-of-band (host `uv`, manual
edits), `make test`/`lint` run against the stale venv with no warning —
run `make install` to resync. The uvicorn command keeps plain `uv run`.

## Commit to `main`; CI gates on push

**Decision:** solo development commits directly to `main` — no branches, no
PRs. GitHub Actions runs the same lint/test gates on every push to `main`
(path-filtered), so nothing loses a check; it just runs post-merge instead
of pre-merge.

**Alternatives rejected:** branch + PR + squash-merge (the original rule) —
the review step has no second reviewer, so the PR round-trip is pure latency.

**Consequence:** a broken commit can land on `main` before CI flags it —
fix forward or `git revert`, never rewrite pushed history. `fix-issue`
agents commit to `main` too; review happens by reading the commit, not a
PR diff. Revisit if a second maintainer joins.

## Architecture docs are generated, not written

**Decision:** `docs/architecture/api/routes.md` is emitted by `make docs`
(`backend/scripts/docs/generate.py`) from the FastAPI route table, then
committed. Hand-written design notes may still live alongside it.

**Alternatives rejected:** hand-maintained docs — they drift and become the
broken windows AGENTS.md bans. Generated DB schema and ER pages — the app
has no database (see "No database").

**Consequence:** `make docs` is part of the route change loop; generation
reads the route table only.


## Frontend style: Prettier + typescript-eslint strict, not a style guide

**Decision:** Prettier owns formatting (`semi: false`, `printWidth: 100` to
match ruff's `line-length`; everything else default). ESLint runs
typescript-eslint's `strictTypeChecked` + `stylisticTypeChecked`, tuned in
`eslint.config.js` where a rule can't model an intentional pattern — each
override carries its reason. `make lint` and CI run `prettier --check`.

**Alternatives rejected:** the Airbnb and Google JavaScript style guides.
Both are frozen and their enforcement is abandoned: `eslint-config-google`
last shipped in 2019 (repo archived 2026-06), `eslint-config-airbnb` has no
ESLint 9 flat-config release and its TypeScript companion is archived.
Adopting either means pinning to dead packages or community forks.

**Consequence:** the rule set moves with typescript-eslint, which ships in
step with TypeScript — upgrades come through Dependabot, not a migration.
Prettier is pinned exact: a minor bump can reflow files, so it lands as its
own reviewable diff.


## Dependabot: auto-merge minor/patch, review majors

**Decision:** `dependabot-automerge.yml` squash-merges a Dependabot PR only
when `dependabot/fetch-metadata` reports a minor or patch update (for a
grouped PR, its highest change) and every check on the PR has passed. It
polls the PR's checks itself, with a grace period for path-filtered
workflows to register. Majors stay open for a human.

**Alternatives rejected:** merging when no checks are visible yet — the
original version did, and in the window before path-filtered checks
registered it merged a red PR (SQLAlchemy 2.1 without greenlet: the
backend couldn't boot). GitHub-native auto-merge — needs required status
checks via branch protection, which this private repo's plan lacks.

**Consequence:** the PR's checks are the only gate: merges made with
`GITHUB_TOKEN` don't trigger the `push` workflows on `main`. A Dependabot PR
left open means a major bump to review or a red check to fix. Lockfiles are
frozen everywhere (Dockerfiles, entrypoints, `make install`, CI), so a
stale lock fails loudly instead of being silently re-resolved.

## Evidence labels are checked references, not verified conclusions

**Decision:** every claim in a debug answer is one fact with an evidence
label (schematic, ERC, datasheet pin table, measurement, or none) and the
references it rests on. `features/debug/evidence.py` keeps the label only
if every reference resolves against the parsed input: the pin is on that
net, the pin has that name, the ERC entry id exists, the pin is in the pin
table, the measurement was entered. It also checks that every pin and net
the text names is cited. Otherwise the claim drops to `none` with
`downgraded` and a reason the UI shows. The UI words labels as "Based on
your schematic", never "Confirmed".

**Alternatives rejected:** letting the model label its own claims
CONFIRMED/INFERRED (the go/no-go prompt). It labelled a DMM reading it was
never given as CONFIRMED. A second model call to judge whether each claim
follows from its references: twice the cost and latency on a call that
already takes ~2 minutes, and it is the same kind of judgement that failed.

**Consequence:** a kept label promises the cited facts exist, not that the
reasoning from them is right. A wrong conclusion drawn from true facts
keeps its label, so claims are kept to one fact each to keep that gap
small, and the evals measure the rest. Free text outside claims (rework
steps, summaries) isn't checked; the prompt carries those rules. The
checker is text matching over refs like `U2.9` and net names, so prose
like "pins 7 and 8" slips past it.

## Datasheet facts come from memory until retrieval

**Decision:** for the hackathon MVP, nothing fetches or reads datasheets.
The model recalls part facts (reference voltages, thresholds, internal
pull-ups, recommended circuits) from memory, and the prompt treats every
recalled fact as unverified: it has to say so, name the datasheet section
that would settle it, and never call part of the design an error or
recommend rework on an unverified fact alone. If the schematic looks
correct for the symptoms, it says so and ranks causes outside the
schematic. The hand-written pin table (plan 001) still covers pin names
for a handful of ICs.

**Alternatives rejected:** per-part datasheet retrieval now (part number
→ PDF → pin table, key specs and reference circuit, cited by page). It's
the right next feature, but finding the right PDF from a value field
that often says "LDO" or "~", and the extra latency on a call that
already takes ~2 minutes, don't fit the hackathon. Checking the whole
schematic against every datasheet, pin by pin: that's a design-review
product for engineers before fab (Flux and others), not a debugger for
a board on the bench.

**Consequence:** the evals found every planted fault from memory alone,
so retrieval isn't needed to find causes; it's needed for trust. Without
it the model invents specs (two runs gave the RT9013 two different EN
thresholds), invents chip features (an STM32F030 option byte that doesn't
exist), and in the no-fault case called a correct part (the 1k series
resistor on the RP2040 crystal, as in the Pico reference design) a design
error and told the user to rework it. The prompt rule limits what
unverified facts can lead to; it can't make them right. Measured on the
evals: all five planted faults still ranked #1, the made-up threshold
numbers went away, the made-up option byte came back marked unverified,
and the no-fault case passed in one of its two conditions; the other
still suspected the correct resistor, though it held rework until a
datasheet check. A pin table only
fixes pin names, not specs or reference circuits, so retrieval replaces
it as the next feature after the hackathon. Since 2026-11-04 a small
hand-written reference table covers recommended circuits for six parts;
see "Reference designs and calculations are verified evidence".

**Partly reversed (2026-11-07):** the app now downloads each IC's
datasheet; see "Datasheets come from the schematic's URL, then LCSC".
The model reads each PDF once (plan 004 step 2; see "Datasheets are read
whole, or cut to a page budget") and the review cites those facts by page
as `datasheet` evidence. For an IC whose datasheet wasn't found or
couldn't be read, and for any fact the reading didn't cover, the model
still recalls, and the unverified-facts rule above still applies.

## Datasheets come from the schematic's URL, then LCSC

**Decision:** every IC's datasheet PDF is fetched while the run is
prepared and kept on disk under `data/datasheets/`, keyed by part number,
until deleted by hand. For each IC, in order: a PDF already kept; the
schematic's own `Datasheet` URL, when it serves a PDF; LCSC's product
record for the part's LCSC number, whose `pdfUrl` is the PDF. KiCad
libraries and JLCPCB-assembled boards fill the LCSC field far more often
than an MPN. Each IC gets a status (found with its source, not found with
what was tried, or skipped for a generic value like "~") that goes into
the design summary. Only ICs are looked up; passives have no datasheet
worth reading.

**Alternatives rejected:** an MPN search. LCSC's keyword search sits
behind a bot wall, and getting past it would be circumvention, so a part
with only an MPN is "not found". Paid part APIs (Octopart/Nexar, Digi-Key,
Mouser) need keys and accounts the app doesn't have. A database for the
PDFs: they're files, and the app keeps no database.

**Consequence:** LCSC's endpoint is unofficial and may change or refuse
us. Anything unexpected there is "not found", never an error, and the
requests say who they are (the app's name as User-Agent; httpx's default
agent gets a 403). The URLs come from an uploaded schematic, so the
fetcher only reaches public addresses, checked on every redirect hop;
DNS rebinding between the check and the connect isn't caught, which is
acceptable for a local tool. The first run of a board waits for its
downloads (at most `DATASHEET_TIMEOUT_S` × 3 per IC, in parallel); later
runs read the kept files. On the orbit, rp-micro and rust-board
schematics, 11 of 15 ICs got a PDF: 5 of 6, 3 of 3 and 3 of 6. Two were
skipped as generic, and two missed (an LCSC number LCSC doesn't know, and
an IC with only a manufacturer).

## Datasheets are read whole, or cut to a page budget

**Decision:** before the review, one model call per datasheet copies out
its pins (in the board's package), limits and typical application
circuit, each with its PDF page. A datasheet up to 120k characters
(~30k tokens) is sent whole; a longer one is cut to that budget by page,
ranked by headings (pin descriptions, limits, electrical
characteristics, typical application, boot, reset, and their
"continued" pages first; package, ordering and revision pages never).
The answer is checked (a fact on a page that wasn't sent, or about a pin
the board's part doesn't have, is dropped) and cached in Redis per part
and per prompt, schema, board pins and model.

**Alternatives rejected:** search over chunks (embeddings, a vector
store): another service, and for one part at a time the headings already
say where the pins and limits are. Sending a whole 600-page reference
manual: too slow and too costly per part, and most of it is registers.
PyMuPDF for the text: AGPL. pdfplumber reads the chosen pages' tables;
pypdfium2, which it already depends on, reads every page's plain text
fast enough to choose them.

**Consequence:** a board's first review waits for its datasheet reads
(in parallel, four at a time); later runs hit the cache. A fact on a page
cut by the budget is missed, and the model falls back to memory, marked
unverified, for it. PDFium isn't thread-safe, so PDF parsing is one at a
time per process; only the model calls run in parallel.

## Debug analysis is two model calls: diagnose, then structure

**Decision:** `features/debug` calls the model twice. `prompt.DIAGNOSE`
asks for a free-text diagnosis, worded like the go/no-go prompt.
`prompt.STRUCTURE` turns that diagnosis into `Analysis[Claim]` (claims,
citations, confirm steps, fixes) and is told not to diagnose again: keep
the causes, their order and their conclusions. The diagnosis is logged
whole. The pin table counts as verified: a fault that follows from the
netlist, ERC and pin table alone is a design error, not an "unverified"
one.

**Alternatives rejected:** one structured call, the original design. With
the real bench notes of a private board (not in the repo) it missed the
board's wiring fault 2 of 2 times, ranking weak source and overload instead, while the
plain go/no-go prompt on the same input ranked it #1. Format rules took
the model's attention away from tracing the circuit. Trimming the format
rules: the citation checker depends on them.

**Consequence:** an analysis takes two ~90–150 s calls, about 4–6 minutes
in all, so the UI shows a separate "Write-up" stage. The second call can
still drift from the diagnosis; on the runs below it didn't. Without the
pin-table rule, one two-step run saw the short, called it "unverified"
under the datasheet-facts rule, and ranked it #3. Measured with the final
prompts: the private board's notes ranked its fault #1 in 2 of 2 runs with no
downgraded claims, all six planted eval faults ranked #1, and the no-fault
case passed. `evals/gonogo.py` doesn't run these prompts, so a green
go/no-go says nothing about the app; `make eval-app` does run them.

## Wiring faults the pin table proves are found by code, not the model

**Decision:** before the model runs, a check (now `roles.check`) tests the
netlist against the pin table. A pin the table marks as an inductor
connection (TPS63070 L1/L2) that shares a net with anything but an
inductor or a test point becomes a verified finding (`wf1`, …). It goes
into the design summary and into the model's input, and the prompt says
findings are design errors to rank on how well they explain the
symptoms, never recalled facts. The UI labels each hypothesis by rule,
not by the model: "Verified design fault" when a backed claim cites a
flagged pin (or a flagged calculation, see below), "Fits the symptoms" when it explains at least half of them
with a backed claim, "Speculative" otherwise. Beside the label sit two
counts (symptoms explained, claims backed), never a percentage. A small
diagram per hypothesis is built from the netlist by the backend
(`diagram.py`), with flagged pins in red.

**Alternatives rejected:** more prompt wording. The pin-table rule only
fires when the table states a rule ("tie to GND if unused"); on a real
board the model saw a regulator's L2 pin on its output rail, quoted the
pin table, then called "L2 must be separate from VOUT" recalled topology
and ranked the short #2. Model-made confidence percentages: they look precise and mean
nothing (plan 001, "Trust"). A kicad-cli sheet SVG per hypothesis: whole
sheets, nothing highlighted, and unreadable at card size.

**Consequence:** the check is only as wide as the pin table: inductor
pins of the parts listed in `pins.py`, so today one switching regulator.
No committed eval case has a part in the pin table, so none gets a
finding and the no-fault case stays quiet by construction rather than by
test; unit tests cover the check on a synthetic design. Measured on a
private board (not in the repo): its fault ranked #1 in 2 of 2
`make eval-app` runs and in 2 live runs, with the diagnosis calling it a verified design error instead of
hedging; `rp-micro-no-fault` still claimed no schematic fault. Datasheet
retrieval (see above) is what widens the check to other parts.

**Pin roles (added later, plan 005 step 3):** the check now runs on every
IC, against each pin's role: from its datasheet (the facts extraction
returns one per pin), else the pin table's inductor pins, else its name
(SW, LX, L1, L2; FB; VIN, VOUT, GND). Three rules: a switch node on a rail
or another IC's pin, a feedback pin on ground or on a rail that isn't its
IC's output, and a typical-application part whose two power pins are
joined. A role from the name alone is trusted less: a switch name counts
only on an IC with a feedback or VOS pin, and a name-only FB on ground stays
quiet, since fixed-output versions ground it. That keeps false positives
down at the cost of missing a regulator with neither unless its
datasheet is read. `orbit-l2-shorted` now gets its finding from U15's pin
names, and the other seven eval boards get none, so the no-fault case is
quiet by test, not by construction.

**Label causes (added later):** a short is often a label, not a wire. A
second same-named label placed on the wrong node joins two nets, the
power symbol's name wins, and the netlist keeps no trace of the label.
So when `roles.check` flags a pin, `roles.label_causes` looks for KiCad
ERC `multiple_net_names` entries that merged a label into that net on the
IC's sheet. It prefers a label named after the pin (`SW_L2` for L2),
then finds the label in the uploaded sheets by the ERC item's uuid
(`design.parse_labels`) and names its file, its position and any other
labels with the same name. The prompt says the fix is then that label,
not rewiring. Rejected: computing connectivity from the schematic's
geometry (pin positions under rotation and mirroring, wires, junctions),
which is too much for its value. The cost: this only works when ERC
reports the merge. That's always true for a label joined to a power net
or another label, but it isn't when the wrong label is the only name on
both nets. `make eval-app` cases have no sheet files, so they get no
label cause. Positions come from the `.kicad_sch` files, because the ERC
JSON's `pos` is 1/100 of the millimetres it claims (KiCad 8.0.9). Measured on
a private board (not in the repo): where the finding alone got "remove
L1.2 from +3V3", the review now ranks the short #1 and its fix is "move
or remove the misplaced label … do not rewire pins".

**Transistor pinouts (added later):** a three-pin part's symbol names its
pins after the datasheet (G, S, D), so `pins.check_pinout` compares those
names with the datasheet numbering in `pins.PINOUTS`. A mismatch is a
verified finding, one per part. It exists because a private board (not in
the repo) drew its AO3400s with `Simulation_SPICE:NMOS` (1=D, 2=G, 3=S) on
a SOT-23 footprint (1=G, 2=S, 3=D): every pad was wrong, the schematic
read correctly, and neither ERC nor the model caught it. `PINOUTS` holds
only parts whose pinout was copied from their datasheet (AO3400, AO3401,
2N7002, BSS138). Any other SOT-23 transistor with G/S/D pins in another
order gets an unverified pin warning (`pw1`, …) from
`pins.check_convention` instead: 1=G, 2=S, 3=D is right for most SOT-23
MOSFETs but not all, and a verified finding tells the user to rework the
board, so a wrong one costs more trust than a missed one. The warning is
shown on the summary card and given to the model in its own section, with
reading the part's pin diagram as the first confirm step; claims can't
cite it. Rejected: applying the convention as a verified finding (wrong
for the exceptions), and fetching the datasheet per part now (see "Datasheet
facts come from memory until retrieval"). The cost: the datasheets draw
SOT-23 leads with letters, not numbers, so each entry reads its numbering
off the standard SOT-23 lead positions, not a numbered table. On that
board all three transistors are flagged; how the model ranks the finding
hasn't been measured yet.

## Review mode reuses the debug pipeline

**Decision:** pre-fab review (`mode=review` on `POST /debug/analyze`) is
the same upload, parse, two model calls and evidence check as debugging,
with its own prompt pair (`prompt.REVIEW_DIAGNOSE`, `REVIEW_STRUCTURE`)
and the same output schema. Symptoms become optional notes on what the
board is for. Each issue gets a `severity` (will_fail, may_fail,
improve; null when debugging), confirm steps become checks to settle the
issue before ordering, and rework stays empty. "No issues found" is a
valid answer, and the prompt forbids padding the list. In review mode the
UI never says "Fits the symptoms": only a backend check (wiring finding,
flagged calculation) makes an issue "Verified"; everything else is
"Reasoned: check it".

**Alternatives rejected:** a separate feature or schema for review. It
would duplicate the parser, checker and diagram for one prompt change,
and break the self-similar naming (one concern, one feature). Labelling
review issues "backed" when their claims cite real pins: on the review
evals, the real faults and the false alarm cited the same mix of
schematic facts, so the label would have meant nothing.

**Consequence:** one endpoint and one UI serve both modes; old runs keep
their mode in `DesignSummary.mode`. Without symptoms nothing anchors the
answer, so invented design faults are the main risk; reference notes
(below) are the mitigation, and `make eval-app mode=review` measures it.

## Reference designs and calculations are verified evidence

**Decision:** `features/debug/reference.py` and `calc.py` add two kinds
of verified input, both before the model runs. **Notes** (`rd1`, …) are short facts
copied from the manufacturer's own datasheet, application note or
hardware design guide, with the section, for RP2040, MCP73831, SY8088,
ESP32-C3-MINI-1, AMS1117 and CH340. **Calculations** (`cf1`, …) apply a datasheet formula to
resistor values in the netlist: a regulator's output from its feedback
divider (SY8088: 0.6 V × (1 + R1/R2)), a charger's current from its
program resistor (MCP73831: 1000 V / R), a fixed LDO's headroom against
its dropout (AMS1117: 1.1 V typical), and generic circuits: LED
current through its series resistor, the pull-up on each I²C line, RC
delays on enable and reset pins, dividers into IC pins, the CC
pull-downs on a USB-C receptacle, and the capacitors on each IC's supply
rails. Flagged: a
regulator output more than 8% from the voltage its output net is named
for (`+3V3`), an LED with no resistor, an I²C line with no pull-up on the
board, or one pulled up above an IC's supply (VBUS counts as 5 V), and
USB-C CC1/CC2 sharing one pull-down, a CC pin with none, or one outside
5.1 kΩ ±10% (CC pins wired to an IC, a PD controller, are skipped), and
an IC supply rail with no capacitor to ground at all, and an AMS1117 whose
input rail's named voltage leaves less than its 1.1 V typical dropout (on
a rail named for a battery it assumes one Li-ion cell, 4.2 V, and reports
without a flag). Fewer capacitors
than power pins on a rail is reported but not flagged: the RP2040 minimal
design shares one on purpose. Claims
cite either with the evidence label `reference` ("Based on the
manufacturer's design"), and the prompts say a circuit matching a note is
correct as designed.

**Alternatives rejected:** letting the model recall reference circuits
(it called the RP2040's 1 kΩ crystal resistor, the Raspberry Pi design, a
fault). Letting the model do the divider arithmetic: it usually gets it
right, but a verified number is what lets the UI say "Verified". Copying
values from third-party datasheet mirrors: the RT9013 has no notes
because Richtek's own PDF couldn't be downloaded.

**Consequence:** the table is narrow, six parts (`pins.py` has four), and
every entry has to be read in the source document, so it grows slowly
until datasheet retrieval replaces both. Measured: review mode on
`rp-micro-no-fault` went from one false alarm to "no issues", and the
swapped divider is ranked #1 in both modes as verified, citing the
calculated 0.73 V. The SY8088 source is an application note (Rev. 0.1),
not a full datasheet. The named-voltage parse covers names like `+3V3`,
`5V` and `1.8V`; a rail named `VCC` gets the calculation but no check.

## Wiring checks trust pin names and vendor pin data, never net names

**Decision:** the `uart` check (`calc.py`) reads a pin's direction only
from its own name (`TXD`, `U0RXD`, `USART1_TX`) or, for MCU pins named
by port (`PA9`, `GPIO0`), from `UART_PINS`: ST's STM32_open_pin_data XML
for STM32F030/G030/F103 and Table 279 of the RP2040 datasheet. Net names
are never used. It flags only nets between ICs: connector pins, CAN and
LIN transceivers (whose input is named `TXD`), and pin pairs that share
a non-direction token (`RXD/GPIO20` on `GPIO20/U0RXD`, a board-outline
symbol carrying the chip's own signal) are skipped. The
`overvoltage_pullup` check flags a pull-up above an IC's I/O supply,
except on STM32 parts, where it's an unflagged hint.

**Why:** a net is named from one end's point of view. `USART1_TX` is TX
for the MCU and RX for the bridge, so reading direction from the name
would raise a false alarm on half of all correct boards. On STM32 parts,
many pins are 5 V tolerant ("FT" in the datasheet pin table), and the
netlist can't say which pins those are. A flag there would be a verified
false alarm on correct boards.

**Alternatives rejected:** direction from the net name (false alarms, as
above). Flagging STM32 over-voltage pull-ups anyway (wrong on every FT
pin). Copying STM32 alternate functions from third-party datasheet
mirrors: ST's own XML on GitHub has the same table, and st.com doesn't
serve its PDFs to this machine.

**Consequence:** an MCU outside `UART_PINS` whose UART pins are named
only by port gets no UART check. Each new family is a table entry read
from the vendor's data. An I/O supply skips core-only rails (`DVDD`,
`VDD_CORE`): the RP2040's 1.1 V DVDD once made every 3.3 V I²C pull-up on
an RP2040 look like over-voltage. Measured offline on all seven eval
cases: `uart` flags only `orbit-uart-swapped`, and `overvoltage_pullup`
flags nothing.

## Japanese changes the write-up, not the diagnosis

**Decision:** with the UI in Japanese, only the user-facing model calls
(structure and the simulation questions) get a rule to write in Japanese
(`prompt.in_language`). The diagnosis call stays English, and so does the
backend's own text: wiring findings, calculations, ERC and downgrade
reasons. Refs, pins, nets, ids and units stay in ASCII in the Japanese
prose. The evidence check matches names on ASCII word boundaries
(`re.ASCII`), because Japanese has no spaces.

**Why:** the diagnosis prompt is the one the evals tuned to find real
bugs, and the user never reads its output, so translating it risks
accuracy for nothing. The check cites refs and nets, so they must stay
exactly as the netlist writes them. Under Python's Unicode `\w`, `+3V3に`
didn't name `+3V3`, so every Japanese claim would have lost its label.

**Alternatives rejected:** running the whole pipeline in Japanese (would
need the evals re-run in Japanese). Translating the model's English answer
afterwards (a third call of about 90 s, and a translator can mangle names).
Translating the backend's check text (message keys across about 1,800
lines of tested code, right before the hackathon deadline).

**Consequence:** a Japanese page still shows English check text. One live
review of `rust-board-i2c-vbus` in Japanese found the planted bug at #1
and kept every claim's label.

## SPICE models are untrusted text

**Decision:** a found or uploaded SPICE model is refused, not run, when it
has a `.control`/`.endc` line or includes a file by an absolute path, `~`,
a drive letter or `..`. Only the picked file and the files it includes
are copied beside the deck. The reason goes in the model's status
(`spice_convert.unsafe`).

**Why:** models come from vendor ZIPs found on the web, and from uploads.
ngspice runs a `.control` block's commands, `shell` among them, and an
include can read any file the server can. A vendor library never needs
either.

**Alternatives rejected:** running ngspice in a sandbox (a container or
seccomp profile per run; more machinery than the risk warrants while
vendor models load without either). Stripping the `.control` block and
running the rest (a model that carries one isn't the plain library it
claims to be).

**Consequence:** a model that includes a shared file from a parent folder
is refused even when it's harmless. Vendor ZIPs' test benches do that,
which is why only what the picked file reaches is checked.


## The dev server proxies the API

**Decision:** the browser calls `/api` on the page's own origin, and the
Vite dev server forwards it to the backend (`server.proxy` in
`vite.config.ts`; compose sets `API_PROXY_TARGET=http://backend:8000`).
`VITE_API_URL` is unset by default and stays only as an override for an
API served elsewhere.

**Why:** the frontend was built to call `http://localhost:<backend port>`.
Opened from another machine on the LAN, that `localhost` is the viewer's
own machine, so every request failed with "Failed to fetch" before it left
the browser. Same-origin works from any host name or IP without config.

**Alternatives rejected:** writing the Mac's LAN IP into `VITE_API_URL`
and `BACKEND_CORS_ORIGINS` (breaks whenever the IP changes, and needs
both edited together).

**Consequence:** CORS only matters when `VITE_API_URL` is set. HMR follows
the page's host instead of a fixed `localhost`. The production image
(nginx) still needs `VITE_API_URL` or its own `/api` proxy.

## Wiring faults are judged by the wiring check, not a simulation's waveform

**Decision:** a wiring fault a vendor-model run might show (a pin shorted
to a rail, a swapped divider) counts as caught when the wiring check
finds it from pin data. A simulation's waveform is evidence beside that
finding, never the test of it: no eval case or plan acceptance requires a
transient detail (a current burst, a ringing edge) to appear.

**Why:** the same deck gives different transients on different ngspice
builds. The TPS63070 L2-to-VOUT study's bursts of more than 10 A into
U5.9 appear in macOS libngspice 45.2 (as KiCad 10 ships it) and in none
of Linux ngspice 43 or 44.2, the builds the container can run. A verdict
on that detail depends on the engine, not the board. The wiring check
depends only on the netlist and the datasheet, and it finds the fault on
both the private board (U5.9, plan 005) and `orbit-l2-shorted` (U15.7).

**Alternatives rejected:** pinning the container to ngspice 45.2 (built
from source, a heavier image, and the next release can move the result
again). Accepting a run only when several engines agree (several
toolchains for one check).

**Consequence:** plan 006 closed on the wiring check (2026-11-12). A
simulation can still flag a fault (the start-up's min corner flags the
orbit board), but an eval doesn't fail when it doesn't.

## Runs are recorded as files

**Decision:** every analysis run appends what it did to
`data/runs/<run id>.jsonl` (`DEBUG_RUN_DIR`, `features/debug/record.py`):
the input (the files as SHA-256 digests, not the files), the context the
model read and its answer is checked against, each model answer and every
SSE event as sent, then each follow-up question and its answer (the
browser sends the run id it got in the design summary). `make replay
run=<id>` checks the recorded answer again with the current evidence
check, with no model call.
Records are kept until deleted by hand, like `data/datasheets/`.

**Why:** before this a run's state was spread over the SSE stream, the
rotating app log, the answer cache and the browser. Nothing could
re-check a past answer after a change to the evidence check, and a
result that looked wrong had its input rebuilt by hand (plan 007).

**Alternatives rejected:** a database (one append-only file per run is
all a replay needs, and it reads with `jq`); keeping the uploaded files
(the record keeps the input built from them, which is what a replay
reads); pruning by age (a record is a few hundred kB; easy to add when
the folder grows).

**Consequence:** state outlives a request, as files (see "No database").
Writing fails open, like the answer cache: a run whose record can't be
written goes on unrecorded, with one warning in the log. With no auth, a
public deploy keeps every visitor's input on disk (see "No auth").

## A mux's model is probed before it goes on the chain

**Decision:** before the chain's decks are planned, each mux or load
switch on a chain with a model that loads gets one short probe run
(`spice_circuits.probe_muxes`): its best-ranked input at its rail's
voltage, a fixed 0.1 A load, 1 ms. The probe's input and output
currents decide how it goes in. Below 10 % of the output current at the
input, its output goes through a zero-volt sense source and an F-source
draws that current from the chain's node above it. Otherwise the model
goes in as it is. A probe that times out, fails or doesn't bring its
output up leaves the mux an ideal switch, as before, with the reason in
the run's setup. The probe runs through the same cache as the decks, so
a model is probed once per input and voltage. A model whose output
doesn't come up from the probe's 50 µs ramp is probed again with its
input stepped on at 0 s; if it comes up then, every run on its chain
steps the chain's source on at 0 s instead of ramping it, and says so in
the setup. Behind the bench corner's current-limited supply its input
can't step (the limit charges the input's caps as a ramp), so there such
a mux stays an ideal switch, saying why.

**Why:** TI's TPS2121 model makes its pass FET's drain a voltage source
to ground copying its input, so the source above it supplies a fraction
of a milliamp while the load draws hundreds (plan 008). An input ammeter
on that chain reads nothing, and the bench supply's current limit is
never reached. A model that does draw its load would draw it twice
under a blanket wrapper. The same model never turns on from an input
ramped up from 0 V (1 µs to 500 µs ramps, runs up to 10 ms: its output
stays under 4 mV), but does from a step; the app ramps every source, so
without the stepped retry the model could never go on a chain.

**Alternatives rejected:** always wrapping (doubles the input current of
a model that conserves it); a list of models known to need it (the next
vendor model isn't on it, and the probe costs one short run); deciding
from the netlist text (a model's current path isn't readable from a
behavioural source's expression in general). For the ramp: finding and
forcing the model's power-on latch inside its netlist (one model's
internals, and the vendor's text is untrusted input we don't edit), or
keeping the ideal switch for it (that model is the plan's point).

**Consequence:** the probe's run time counts against `SPICE_BUDGET_S`.
With a wrapped mux the input ammeter reads the load through the F-source
plus whatever the model draws from its input itself. The wrapper
draws from the input node the chain came through, not the one the
model's priority logic picked, which is the same input whenever the
chain's source is the only one powered, as in every run today. On a
stepped chain the inrush the source sees is the step's, through the
mux's own soft start, not a 50 µs ramp's. The bench corner doesn't run
such a model: on the private board, TI's TPS2121 behind a 1 A limit
stayed off even with its input held at 4.8 V by an initial condition.
