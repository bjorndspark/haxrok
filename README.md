# Häxrök

*Hunt down the fault before the magic smoke escapes.*

Häxrök (Swedish for "witch smoke") is a schematic debugger. You give it your
KiCad netlist and describe the symptom you see on the bench. It ranks root-cause
hypotheses and labels every claim *confirmed* (follows from the netlist, ERC
or the part's datasheet pins) or *inferred*. The next step, planned, is to
**check the top hypothesis with an ngspice simulation**.

Most AI schematic tools are linters that run before fab. Häxrök works the other
way round, starting from a board that already misbehaves.

Built for the Meta Global AI Developer Hackathon (2026).

## Status

Work in progress.

Scope, the go/no-go result and the ideas weighed against this one are in
[`docs/plans/001-100/001-mvp-scope.md`](docs/plans/001-100/001-mvp-scope.md).

- [x] Eval harness and the first real case (`evals/`)
- [x] Go/no-go: does the model find the bug? It found it #1 from the
      netlist alone, so it's a go (`evals/README.md`)
- [ ] MVP debug feature: KiCad project folder drop (netlist and ERC are
      extracted by the backend), symptom box, pin-function
      lookup, ranked hypotheses with source labels
- [ ] Stretch: ngspice check with waveforms, agent loop, pre-fab review
      mode

The app code is still the template it was started from (a FastAPI,
React and Postgres template). The seed chat, voice and image features are
still there and will be replaced by the debug feature.

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
