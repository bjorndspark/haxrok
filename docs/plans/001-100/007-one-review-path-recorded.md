# Plan 007: One review path, recorded per run

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-12 | 2026-11-12 | in progress (steps 1–2 done) | — | `DECISIONS.md` ("No database", "Evidence labels are checked references", "Debug analysis is two model calls"), `evals/app_prompts.py` |

A review of the debug pipeline against the usual advice for apps built
around a model ("keep the core small, keep one append-only log as the
source of truth") found two gaps.

**The review stage is one 136-line function, and the evals copy it.**
`service._review` (`service.py:436`) reads the datasheets, builds the pin
table, power tree, calculations and deviations, writes the progress text,
builds the user message, makes both model calls, checks the evidence and
draws the diagrams. `evals/app_prompts.py` rebuilds the same input by hand
("as `service._review` builds it"), and it drifts: 9648b10 added the `voltage`,
`mcu` and `application` checks the app had for a while and the eval
didn't. Each eval run in that
window measured a prompt the app no longer sent.

**A run leaves no record.** Its state is spread over the SSE stream (gone
when the tab closes), `logs/backend/app.log` (the diagnosis as text,
archived on every restart), the Redis answer cache (gone after
`AI_CACHE_TTL_S`) and the browser, which sends the whole `CheckedAnalysis`
back for each follow-up (`ResultAskRequest.result`). So:

- after a change to `evidence.py` or `diagram.py`, nothing can re-check a
  past run's answer; only a new model call (minutes) shows the effect;
- when a result looks wrong, the input the model saw has to be rebuilt by
  hand, and the follow-up questions about it aren't kept anywhere.

The goal: one function builds the review's input for the app and the
evals, and every run appends what it did to one file, from which the
evidence check can be replayed without a model call.

Acceptance:

- `evals/app_prompts.py` builds its input by calling the same function as
  `service`; no "as `service._x` does" copies are left.
- A live run writes `data/runs/<run id>.jsonl` with the input, both model
  answers, every SSE event and its follow-ups.
- `make replay run=<id>` re-checks a recorded run's structured answer with
  the current `evidence.py` and prints which labels changed, with no
  model call.

Out of scope: a database (the record is files, like `data/datasheets/`);
storing the uploaded files themselves (the record keeps their digests and
the input built from them); the SPICE `.control`/include filter, which
gets its own small fix.

Effort: about 12–20 h, roughly 2–3 working days.

Decided (2026-11-12): work on a loop branch from `dev`
(`/step-loop 007`), after plan 006's close-out (dd67598, 8aa0370) and
plan 008 landed on `dev`.
Decided (2026-11-12): run records are kept until deleted by hand, like
`data/datasheets/`; no pruning. A record is a few hundred kB (the user
message is in it twice), and pruning by age is easy to add when the
folder gets big.
Decided (2026-11-12): a follow-up keeps sending the result
(`ResultAskRequest.result`) and gains an optional `run_id`, used only to
append the turn to the run's record. Loading the result from the record
would make follow-ups fail once a record is deleted, and with no auth
the client can only mislead its own answer.

## 1. One function builds the review's input: done

- A pure `context.build(...)` (new `features/debug/context.py`) takes
  what `_review` reads today (the design, ERC, symptoms, measurements,
  references, calculations, mode, pin lookup, and the datasheet reading)
  and returns a frozen `ReviewContext`: the pin table, power tree,
  calculations, deviations, the user message, the `EvidenceContext`
  and the counts the progress text needs.
- The progress text and the `datasheet_facts` event are built from the
  context by pure functions, not inline.
- `_review` keeps the I/O only: await the reading, yield events, call
  the model twice, check, draw. `_wiring` and `_budgeted_tree` move into
  `context.py`.
- `evals/app_prompts.py` calls `context.build` and drops `wiring`,
  `budgeted` and the body of `build_input`.
- No behaviour change: the user message stays byte-for-byte the same.

**Check:** `make lint`, `make test`, and a test that pins the user
message for a fixture design (written before the move, so the move
can't change it). Then `make eval-app` on one case whose answer is
already cached: "served from cache" for both calls proves the prompt is
byte-for-byte the same, since the cache key hashes the messages.
Effort: 4–6 h.

Decided (2026-11-12), default taken in loop: `context.build` takes an
optional `erc_report` text, used in place of the formatted `erc` entries.
An eval case's ERC is kicad-cli's text report, not parsed entries, and
the eval prompt had to stay byte-for-byte the same.
Decided (2026-11-12), default taken in loop: `_fixes` moved into
`context.fixes` along with `_wiring` and `_budgeted_tree`, since the
eval's `simulate` copied it too ("as `service._fixes` does").
Decided (2026-11-12), default taken in loop: the pin lookup is always
rebuilt with the reading, dropping `_wiring`'s shortcut back to
`prepare`'s table when nothing was read. With nothing read the two are
equal (checked on all 8 eval cases), and the warnings never depended on
the reading.
Decided (2026-11-12), default taken in loop: the pinned messages are
golden files, `backend/tests/golden/review_input_{debug,review}.txt`,
written from the code before the move.

**Checks:** `make lint` passes (one ESLint warning, in frontend code
this step doesn't touch). `make test`: 410 backend tests and 52 frontend
tests pass, including the two new golden tests, debug mode with a datasheet
read and review mode. `make eval-app` served both calls from cache for
`rust-board-fb-swapped` (no datasheet found) and for `orbit-l2-shorted`
(U15's datasheet read, wiring finding U15.7). (Both `make lint` and
`make test` ran target by target, since make 3.81 lacks
`--output-sync`.)

## 2. Every run appends to its own record: done

- `features/debug/record.py`: a `Recorder` protocol with
  `append(kind, data)`, a file recorder that writes one JSON line per
  call to `data/runs/<run id>.jsonl` (`DEBUG_RUN_DIR`, git-ignored), and a
  no-op recorder for tests. Timestamps come from `get_clock().now()`.
  Writes fail open, like the answer cache: an error is logged and the
  run goes on.
- The router makes the run id and passes a recorder to `stream`.
  `service` takes it as a dependency, like the model calls.
- Lines, in order: `input` (mode, language, symptoms, measurements,
  analyses, file names with SHA-256), `context` (from step 1: the user
  message and the ids the evidence check accepts), `model` (one per call:
  name, which prompt, the raw answer, seconds), `event` (each SSE event as
  sent, the result included).
- The run id goes to the browser in `DesignSummary` so a report and a
  follow-up can name it; the UI doesn't show it yet.
- `DECISIONS.md`: add "Runs are recorded as files". It changes
  "No database" (state now outlives a request, but as files) and "No
  auth" (a public deploy now keeps everyone's input on disk).

**Check:** `make lint`, `make test` (a faked run writes the expected
lines in order; a failing recorder doesn't fail the run), then one live
run, reading its record. `make docs` if the summary's schema shows in
the routes doc.
Effort: 4–6 h.

Decided (2026-11-12), default taken in loop: the `context` line holds
the design whole as JSON (`record.design_data`: each part and each net's
nodes, which `Design(parts, nets)` rebuilds), the ERC entries, pin data,
the evidence ids, the wiring findings and the calculations. That leaves
step 3's choice open: the compact netlist is in the user message, the
JSON beside it.
Decided (2026-11-12), default taken in loop: the run id is
`<UTC yyyymmdd-hhmmss>-<6 hex>`, so records sort by start time; a run
recorded with the no-op recorder has an empty `run_id` in its summary.
Decided (2026-11-12), default taken in loop: `context` and `model` lines
are written when the review makes them, so they can come before events
the review has queued but the stream hasn't sent yet; the test checks
order within each kind, not across kinds.

**Checks:** `make lint` passes (ESLint warnings in frontend code this
step doesn't touch). `make test`: 413 backend tests and 52 frontend tests
pass, including a faked run whose record has the input (file digests),
context (a `Design` rebuilt from it), both model lines and every event in
order, a recorder that can't write (a file where its folder should be)
that logs one warning while the run still ends in a result, and the
route test reading the record its SSE names. One live run of
`rust-board-fb-swapped` (7.5 min, nothing cached) wrote a 140 kB record:
input, context (55 parts, 90 datasheet ids, 17 references), the
diagnosis (131 s) and analysis (173 s) model lines, and every event
through `result`. `make docs`: the routes doc doesn't change.
Bugs found: the route tests wrote real records into `data/runs/` until
`fake_backends` pointed `DEBUG_RUN_DIR` at a temp folder. Not fixed
here: a `make test` run during the live run rotated `logs/backend/app.log`
under the dev server, and the live run's log lines (its `run <id>` line
included) are in neither the log nor its archive; the tests share the
server's log file.

## 3. Replay and follow-ups from the record

- `make replay run=<id>`: reads a record, rebuilds the `EvidenceContext`
  from its `context` line, parses the `debug_analysis` answer as
  `Analysis[Claim]`, runs the current `evidence.check` and
  `diagram.build`, and prints each claim whose label or downgrade reason
  changed against the recorded `result`. No model call, no Redis.
- `/debug/ask-result` takes an optional `run_id`; with one, the question
  and answer are appended to that run's record as `ask` lines. The
  frontend sends the id it got in the summary.
- The `context` line has to hold what `EvidenceContext` needs (the
  design's nets and pins, ERC ids, pin refs, ids); step 2 records it in a
  form step 3 can load back. Decided when the step is: whether the design
  is rebuilt from the compact netlist (`evals/` already parses it) or
  stored as its own JSON.

Big: touches the backend and the frontend.

**Check:** `make lint`, `make test` (replay of a recorded fixture run
reports no change; with a changed checker it reports the change), one
live run with a follow-up, then `make replay` on it.
Effort: 4–8 h.
