# Plan 003: Simulate in parallel, then ask about the result

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-04 | 2026-11-09 | Done | — | `001-mvp-scope.md`, `002-verified-wiring-checks.md` |

Until now, ngspice ran inside `service.prepare`, one deck at a time, before
the stream opened. Its plot was only drawn under a finding that cited the
calculation, and nothing could be asked about it. In practice, most runs
showed no plot at all.

An engineer simulates, looks at the waveform, then asks about it ("why does
EN cross 63% so late?", "is that enough margin?"). This plan makes the app
work the same way.

## 1. Simulations run in parallel and stream as they finish: done

- `spice.plan(calcs, design)` picks the circuits to simulate: a flagged I²C
  line with a pull-up, an RC delay on an enable or reset pin. It's pure, so
  the next circuit type is one more branch.
- `spice.simulate(jobs, runner)` runs the decks concurrently (at most 4
  ngspice processes) and yields each `Simulation` as it finishes. A failed
  run yields nothing; it never fails the request.
- `service.stream` merges the simulations with the model's events. A
  `simulation` event (id `sim1`, …, `calc_id`, title, deck, waveform)
  arrives while the model is still thinking. `result` or `error` is held
  back until the simulations are done, so it stays the last event.
- The model doesn't see simulations in the review. That keeps the review
  from waiting on them.
- UI: each simulation is drawn under its calculation in the design summary
  (while analyzing) and in a "Simulations" card on the finished run, whether
  or not a finding cites it.

## 2. Ask about a simulation: done

- `POST /api/v1/debug/ask` takes `{simulation, question, history}` and
  returns `{answer}` in the envelope. It's stateless: runs live in the
  browser (plan 001: no accounts), so the browser sends the simulation back
  with each question.
- The prompt (`prompt.ASK_SIMULATION`) gives the model the deck, the
  caption, the reference lines and a sample table. It must answer from those
  numbers, and it must say when the answer needs a datasheet value the
  simulation doesn't have (an EN threshold, a bus capacitance).
- UI: a question box under each plot, with the conversation kept in the
  component (TanStack Query mutation for the call).

## 3. What-if re-runs: done

"Try C5 = 1 µF" would re-run ngspice with the changed value and draw the new
curve over the old one.

Decided (2026-11-08): the user types the new values, not the model. Each RC
plot gets R and C fields and a Re-run button. The backend re-runs ngspice
(about 0.1 s) and the new curve is drawn over the old one, with no model
call. A question asked afterwards sees both traces. The model proposing a
change in a structured answer (the earlier sketch) was rejected: it costs
two model calls per what-if and depends on the model reading the intent.
Decided (2026-11-08): only plan 003's RC runs (I²C lines, enable/reset
RCs) get the fields. A vendor-model run (plan 004) takes 6–15 minutes and
would need its circuit re-cut, so it keeps only the ask box.
Decided (2026-11-08): a plot keeps the original and the latest what-if.
Each re-run replaces the previous what-if, and Reset drops it.
Decided (2026-11-08), defaults:
- the fields are R and C, labelled with the part refs where the netlist
  has a single part (`R5`, `C5`). On an I²C line, C is "bus C
  (estimated)". The rail voltage can't be edited. Values take SI suffixes
  (`4.7k`, `1u`, `1µ`, `100n`) and are parsed in `present.ts`. The
  backend rejects values outside 1 Ω–100 MΩ and 0.1 pF–0.1 F with a 400.
- `POST /api/v1/debug/rerun` takes `{simulation, r_ohm, c_farad}` and
  returns `SuccessResponse[Simulation]`. It's stateless, like `/ask`.
  `Simulation` gains an optional `circuit` (kind, net, R, C, rail, the
  IC's supply, part refs), set only on RC runs, so the backend can
  rebuild the deck.
- both traces share one time axis, long enough for the slower one. The
  what-if is dashed. The caption gives both runs' numbers ("rise time
  120 ns → 48 ns").
- the what-if is kept in the Zustand store per run and simulation, like
  the ask turns. The call itself is a TanStack Query mutation.
- `AskRequest` gains an optional `what_if`. In `ASK_SIMULATION`, the RC
  rule changes. With a what-if, the model compares the two sets of
  samples. Without one, it still gives the R×C estimate and points to the
  Re-run fields. `ASK_SIMULATION` isn't one of the debug prompts, so
  there's no `make eval-app` run.
- the Markdown report leaves what-ifs out.
- the step touches both backend and frontend, so it's big and is built in
  its own session. `make docs` runs for the new route.

**Checks:** `make lint`, `make test`, with tests for the re-run deck and
caption, the bounds, the value parser and the overlay layout; a re-run
from the UI on an I²C line and an RC delay.

**Checks (2026-11-08):** lint is clean (ruff, ruff format, mypy, ESLint,
tsc, Prettier; ESLint keeps its old fast-refresh warning in
`shared/ui/button.tsx`). Tests: backend 281 passed, frontend 44 passed.
New backend tests cover the `circuit` on both RC kinds, the re-run deck and
caption, the "estimated" wording dropped once C is typed, the bounds (both
edges accepted, NaN refused), a vendor run refused, ngspice failing
(`SIMULATION_FAILED`), the route's envelope and 400, and the ask message
carrying both runs. Frontend tests cover `parseSi` (`4k7`, `1µ`, `1μF`,
`1meg`, `470m`, `4.7 kΩ`, refusals), `formatSi` round trips, the field
labels, the shared axis and hover readout, and the Markdown report leaving
what-ifs out. Live against the running backend with real ngspice, an I²C
line at 2.2 kΩ went from 119 ns to 55.9 ns (rise 30→70%); at 100 pF it went
to 398 ns and lost the "estimated" wording. An RC delay at 4.7 kΩ went from
10 ms to 4.72 ms to 63%, and at 4.7 µF to 47.2 ms. C = 1 F gave the 400. The
re-run wasn't clicked in a browser: that needs a full model run to put
simulations on the page.

**Bugs found:** the overlay test expected a µs axis for a 2.2 µs what-if,
but 2200 ns is still under the 10,000-unit limit, so ns is right (the
backend picks the same way). The test now uses a 22 µs what-if.

## 4. An EN/reset RC checked against its rail's ramp: done

A single RC needs no simulator. ngspice is worth having for circuits a
formula can't answer: the ESP32 EN RC against the regulator's soft-start
ramp, a supervisor's release against the rail, a low-side FET's gate drive.
Each is a new `plan` branch plus a deck builder.

Decided (2026-11-09): step 4 drives plan 003's EN/reset RC deck from a rail
that ramps up over the regulator's datasheet start-up time, instead of a
step at t=0, and checks that EN crosses its threshold only after the rail
is in band. It needs no vendor model, so it runs on boards whose regulator
has none (rust-board's ESP32-C3, rp-micro). Plan 004 step 6 already covers
the same check with a vendor model; a low-side FET's gate drive is left
out, since no demo board has one to test against.
Decided (2026-11-09): without a start-up time (no datasheet, or none in its
facts), the EN/reset run stays plan 003's step at t=0 with no ramp check,
and its caption says why. No assumed ramp, as plan 004 does for a missing
rated current.
Decided (2026-11-09): when the start-up time is known, the ramped run
replaces the step run for that pin, so each EN/reset RC still has one
plot. Step 3's what-if re-runs it with the same ramp.
Decided (2026-11-09): the ramped run carries a `verdict` (`passed` or
`flagged`), drawn as plan 004's vendor-model runs are, and its text gives
the release time and when the rail reaches its band. The model's review
doesn't see it, as in plans 003 and 004.
Decided (2026-11-09), defaults:
- EN/reset counts as released at 0.7 × the rail, and the rail's band is
  the regulator's output accuracy (±5 % without one): plan 004's numbers,
  so both kinds of run judge a reset the same way.
- The rail rises linearly from 0 V to its nominal voltage over the start-up
  time plan 004 already parses (`spice_circuits.py`), shared inside the
  debug feature rather than parsed twice.
- The regulator is the one whose output net is the RC's rail. A rail that
  doesn't come from a regulator (USB, a battery) keeps the step run.
- Checks: unit tests on the ramped deck, the verdict and the fallback; a
  live run in the backend container on rust-board with the ESP32-C3's EN
  capacitor made small so EN releases early. `make eval-app` can't run
  simulations, so it isn't used.
- The step touches backend and frontend, so it's built in its own session.
- Found in the build: the regulator also counts when its switch pin
  (LX/SW) reaches the rail through an inductor (a buck). rust-board's +3V3
  comes from an SY8088 buck through L1, so without this the step's own
  target board got only the plain step run (see **Bugs found**).

**Checks (2026-11-09):** lint is clean (ruff, ruff format, mypy, ESLint,
tsc, Prettier; ESLint keeps its old fast-refresh warning in
`shared/ui/button.tsx`). Tests: backend 289 passed, frontend 44 passed.
The ngspice fake now solves a linear ramp into an RC analytically. New
backend tests cover `regulator_of` (an LDO, a buck through its inductor,
a rail with none), `ramp` from the facts (±2 % cited; no facts gives no
start-up time and ±5 % assumed; a 3 s soft-start is dropped), the ramped
deck and caption (10 kΩ/1 µF on a 1 ms ramp: released at 12 ms, in band
at 980 µs, passed), a 1 nF capacitor flagged (released at 710 µs), the
step fallback with no start-up time and its caption, a what-if that keeps
the ramp ("Release: 12.x ms → 710 µs", flagged), the stream holding a
regulator-fed RC until the reading arrives (flagged, PWL deck), a rail
with no regulator still streaming a step with no verdict, and the ask
message naming the ramped run and its verdict. Live in the backend
container on rust-board (exported through `kicad-cli`, real datasheet
reader and ngspice): `regulator_of` found U5 (SY8088) for CHIP_PU's
+3V3. Its facts gave a 700 µs soft-start and no output accuracy (±5 %
assumed; in band at 665 µs). As built, R8 10 kΩ / C10 1 µF releases at
12.4 ms: passed. With C10 at 10 nF it releases at 590 µs and at 1 nF at
500 µs, both flagged. A what-if from the as-built run to 10 nF gave
"Release: 12.4 ms → 590 µs", flagged, with the ramp kept. The cards
weren't checked in a browser: that needs a full model run to put
simulations on the page.

**Bugs found:** on rust-board, `regulator_of` first found no regulator:
the SY8088's output is its LX pin through L1, not a VOUT pin, so the
board got the step run. It now also follows a switch pin through an
inductor, with a test. mypy caught `build_ask_message` calling
`.replace` on a verdict that can now be None on an RC run. A local
`DatasheetReading` import in the tests broke ruff's name check; it's
imported at the top now.
