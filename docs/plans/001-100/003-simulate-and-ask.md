# Plan 003: Simulate in parallel, then ask about the result

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-04 | 2026-11-04 | In progress | — | `001-mvp-scope.md`, `002-verified-wiring-checks.md` |

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

## 3. What-if re-runs: later

"Try C5 = 1 µF" would re-run ngspice with the changed value and draw the new
curve over the old one. This needs the model to propose a parameter change
the backend can apply (structured output), and the plot to show two traces.
Not before the deadline.

## 4. Circuits a formula can't answer: later

A single RC needs no simulator. ngspice is worth having for circuits a
formula can't answer: the ESP32 EN RC against the regulator's soft-start
ramp, a supervisor's release against the rail, a low-side FET's gate drive.
Each is a new `plan` branch plus a deck builder.
