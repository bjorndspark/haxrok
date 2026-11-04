# Häxrök

*Hunt down the fault before the magic smoke escapes.*

Häxrök (Swedish for "witch smoke") is a schematic debugger. You give it your
KiCad netlist and describe the symptom you see on the bench. It ranks root-cause
hypotheses and labels every claim with what it rests on: your netlist, KiCad's
ERC, the part's datasheet pins, the manufacturer's reference design, a value
calculated from your resistors, or nothing. Before you order boards, the same
pipeline runs as a **pre-fab review**: no symptoms, just the design mistakes a
reviewer would flag.

Most AI schematic tools are linters that run before fab. Häxrök works the other
way round, starting from a board that already misbehaves.

Built for the Meta Global AI Developer Hackathon (2026).

## Status

The MVP is built: a KiCad project folder runs end to end from the UI,
and it ranked a real board's fault #1. Pre-fab review mode, reference
designs, wiring checks and ngspice simulations are built too.

Scope, the go/no-go result and the ideas weighed against this one are in
[`docs/plans/001-100/001-mvp-scope.md`](docs/plans/001-100/001-mvp-scope.md).

- [x] Eval harness and planted-fault cases (`evals/`)
- [x] Go/no-go: does the model find the bug? On a real bug (a private
      board, not in the repo) it found it #1 from the netlist alone, so
      it's a go (`docs/plans/001-100/001-mvp-scope.md`)
- [x] MVP debug feature: KiCad project folder drop (netlist and ERC are
      extracted by the backend), symptom box, pin-function
      lookup, ranked hypotheses with source labels
- [x] Pin-table wiring check before the model, a rule-based confidence
      label per hypothesis, and a small wiring diagram per hypothesis
- [x] Pre-fab review mode: no symptoms, issues ranked by severity
      (won't work / may fail / improvement), "no issues" allowed
- [x] Reference designs: notes from the manufacturers' own documents for a
      few ICs, and values calculated from the netlist with datasheet
      formulas (regulator output from its feedback divider, charger
      current), flagged when they contradict the design
- [x] Wiring checks in code: USB-C CC pull-downs, missing decoupling,
      SOT-23 transistor pinouts, and from
      `docs/plans/001-100/002-verified-wiring-checks.md` UART wired TX to
      TX, a regulator enabled from its own output, and pull-ups above an
      IC's supply. The STM32 BOOT0 check waits for ST's reference manual
- [x] ngspice simulations of I²C lines and enable/reset RC delays, run in
      parallel with the review and drawn as waveforms. You can ask the model
      about each one (`docs/plans/001-100/003-simulate-and-ask.md`)
- [ ] Stretch: agent loop, what-if re-runs ("try C5 = 1 µF")

The app started from a FastAPI, React and Postgres template. Its
seed chat, voice and image features have been removed; `debug` is the only
feature.

## Quick start

```sh
make dev        # generates .env, starts shared infra, creates the DB,
                # then starts backend + frontend
```

Then open the printed frontend port (default http://localhost:5173).

### Model gateway

All model calls go through an OpenAI-compatible gateway
([Bifrost](https://github.com/maximhq/bifrost)). Set these in `.env`:

```
AI_BASE_URL=http://<bifrost-host>:<port>/v1
AI_API_KEY=<Bifrost virtual key>
AI_CHAT_MODEL=meta/muse-spark-1.3-contributor
```

Provider keys and routing are configured in Bifrost, not here. To switch
models or providers, change the gateway config or these variables. No code
changes are needed.

## Evals

```sh
python3 -I evals/gonogo.py --model meta/muse-spark-1.3-contributor
```

`evals/README.md` explains the cases, how to add one and how to read the
results.

## Layout

```
backend/app/
  main.py            # composition root
  core/              # config, db, logging, errors, ai/ (the only place AI is called)
  features/          # one package per feature
frontend/src/
  app/               # shell: routes, index.css
  features/          # index.ts is each feature's public API
  shared/            # ui/, api client, lib/
evals/               # known-bug cases and the go/no-go harness
```

## Commands

`make dev` · `make lint` · `make test` · `make migrate` · `make revision m='…'` ·
`make docs` · `make down` · `make purge`

Dependencies: `./scripts/docker/uv.sh add <pkg>` / `./scripts/docker/pnpm.sh add <pkg>`
(they run inside the containers, against the pinned lockfiles).
