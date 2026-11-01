# Decisions

The few trade-offs worth recording. Append at the bottom; don't fork docs.

## AI is delegated to an external gateway

**Decision:** the app talks to one OpenAI-compatible endpoint (`AI_BASE_URL`
+ `AI_API_KEY`, model names as env vars). All provider concerns — keys,
routing, fallbacks, budgets, observability — live in the gateway (Bifrost
recommended). `app/core/ai/` is the entire AI surface: one `AsyncOpenAI` client
plus thin media helpers.

**Alternatives rejected:** an in-repo provider abstraction + model catalog
(what Fulcrum built — ~120 files, a second product); embedding a gateway as
a compose service (the repo shouldn't manage infra it doesn't own).

**Consequence:** switching providers/models is gateway config, not a code
change. Features that need provider-specific behavior have nowhere to put it
— that's the point. One narrow exception: `AI_*_FALLBACKS` env vars forward a
`provider/model` fallback list verbatim as the gateway's `fallbacks` request
field (per modality, via `fallbacks_extra_body` in `app/core/ai/client.py`).
It's a pass-through, not routing logic — unset, the gateway's own VK/routing
fallbacks apply; set, they override it for that request type.

Cost protection is the gateway's job too: with no auth (below), a public
deploy lets anyone drive chat, image, TTS, and STT calls, so the budget and
rate limits on the gateway virtual key (`AI_API_KEY`) are the spend
ceiling. The app adds no rate limiting of its own — one place to set
limits, and it's the place that sees every provider's price. Configure
them before exposing a deploy.

## No auth

**Decision:** the API is open. No users table, no tokens, no middleware.

**Consequence:** per-user anything is out of scope until a real need exists.
If auth is added later, add it as a feature package — the structure has an
obvious home for it. Until then a deployed instance is open to anyone who
finds the URL; AI spend is capped at the gateway (see above), and the
unauthenticated log ingest and media endpoints are bounded only by their
request-size limits.

Auth belongs in derivative projects, not the template — many don't need
it, and the first real one defines the requirements. The intended shape:
an `auth` feature delegating identity to a hosted OIDC provider (free
tier; self-hosted Zitadel/Authentik as the swap-in), opt-in via config the
way `AI_BASE_URL` is, with conversations gaining an owner and the
WebSocket and media routes checking it.

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

## Shared infra, per-project database

**Decision:** PostgreSQL (and Redis) live in `docker-compose.infra.yml` —
`name: infra`, identical to the same file in the sibling projects (fulcrum,
volundr), so one infra stack runs per machine regardless of which repo starts
it. `docker-compose.yml` holds only the app containers; the backend reaches
postgres by service name on the external `infra_default` network. Each copy's
database is created lazily by `scripts/db/create.sh` (`make dev` → `db` →
`infra`). `POSTGRES_HOST_PORT` is machine-level (55432), never auto-picked.

**Alternatives rejected:** per-clone postgres inside the app compose (the
original choice — every copy/worktree paid for its own postgres container and
volume, and recreating it per clone was the annoyance that motivated this).

**Consequence:** `docker compose up` alone fails without the infra stack —
`make dev` is the entry point, not the compose file. `make purge` removes app
containers only; the project DB survives in shared infra. Redis is reached
the same way — `REDIS_URL` (compose sets the internal service name), one
shared async client in `app/core/redis.py`.

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
run `make install` to resync. Runtime targets (`migrate`, `revision`, the
uvicorn command) keep plain `uv run`.

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

**Decision:** `docs/architecture/db/schema.md`, `db/er-diagram.md`, and
`api/routes.md` are emitted by `make docs`
(`backend/scripts/docs/generate.py`) from SQLAlchemy metadata and the
FastAPI route table, then committed. Hand-written design notes may still
live alongside them.

**Alternatives rejected:** hand-maintained docs — they drift and become the
broken windows AGENTS.md bans. eralchemy2/graphviz — a system dependency to
draw what Mermaid renders natively on GitHub.

**Consequence:** `make docs` is part of the schema/route change loop
(same step as `make migrate`). No DB connection is needed — generation
reads metadata, not a live database.

## Voice mode is a staged pipeline over one WebSocket

**Decision:** hands-free voice lives in `features/chat` (`voice.py`) on
`WS /chat/conversations/{id}/voice`. The browser runs Silero VAD and streams
16kHz pcm frames up during speech; ~0.5s of low-prob frames sends
`speech_maybe_end` and the backend transcribes the whole utterance buffer
in one shot and runs the turn *speculatively* — STT + LLM TTFB + first TTS
bytes hide under the client's own VAD redemption hold (1.2s). The
speculation is invisible until `speech_end` confirms it: its frames are
held server-side and nothing is persisted (user message and reply commit
together, in one transaction, after confirmation). Speech resuming inside
the hold sends `speech_continue`; a VAD misfire sends `speech_cancel`;
either discards the speculation unseen. The reply's token stream is cut at
any punctuation into TTS fragments (≤2 in flight) that stream back down
the same socket — `audio_begin` → binary frames → `audio_end`. The client
plays pcm straight into an AudioWorklet; mp3 is the per-clip fallback.
STT/TTS calls carry timeouts so a hung upstream drops the turn/clip
instead of wedging the session.

**Alternatives rejected:** sliding-window STT (~1 call/sec of speech with
overlap stitching) — live captions during speech, but 5-6× the requests,
and overlapping windows disagree on wording so partials needed timestamp
dedupe anyway. Full REST like the original implementation — serial stages
plus a synthesize→store→URL→refetch detour per sentence. Provider-native
realtime speech-to-speech — fastest (~0.5s) but locks to realtime-capable
providers, repriced audio tokens, and reshapes transcript persistence.
WebRTC — better loss handling, but the WS already carries both directions
and the LAN/dev setting doesn't justify it.

**Consequence:** one STT call per utterance instead of ~1/sec; a misfired
speculation costs its wasted upstream calls (STT, maybe LLM/TTS) and
nothing else. First audio lands as soon as `speech_end` arrives, with the
upstream latency already paid; it never starts while the user might still
be mid-sentence. Going materially lower needs faster STT/LLM (local
whisper + a quick model) or a shorter redemption, not more orchestration.
Releasing earlier (at transcript time) was the original design — it split
one utterance into two messages whenever STT beat `speech_continue`. No live
partial captions — the transcript appears at end-of-speech. Half-duplex
by default — the VAD pauses while a reply plays; opt-in talk-over keeps it
listening, and a confirmed barge-in (`speech_start` mid-reply) cancels the
turn, keeping the partial reply.

## Server-side echo cancellation is optional and approximate

**Decision:** voice mode can run a second echo canceller on the server
(`VOICE_ECHO_CANCELLER`: `off` | `speex` | `webrtc`), behind the browser's
own getUserMedia AEC which stays the primary line. `app/core/echo.py`
queues sent TTS samples as the far-end reference; mic frames consume it
for their own span, and the wall clock drains whatever played while no mic
audio arrived (the client streams only while the user speaks) — the queue
depth models the client's playback buffer, so clips synthesized faster
than realtime stay roughly aligned. SpeexDSP binds via ctypes (no build
step; `libspeexdsp1` in the image); WebRTC AEC3 via the pywebrtc-audio
wheel, an optional `webrtc` extra (numpy) that images opt into with
`UV_EXTRAS`.

**Alternatives rejected:** client playout timestamps — exact alignment
would need the client to report when each clip actually reaches the
speaker plus a shared capture/render clock; real complexity for a
second-line defence. Python `speexdsp`/`webrtc-audio-processing` PyPI
bindings — sdist-only, need swig + headers + a toolchain in the image;
the ctypes wrapper needs only the runtime .so. Decoding mp3 clips for
the reference — the mp3 fallback session just gets no far-end signal.

**Consequence:** alignment is estimated, not exact — the residual delay
(network transit, acoustic path) is left to each canceller's filter /
delay estimator (`VOICE_EC_DELAY_MS`, `VOICE_EC_FILTER_MS` tune it;
under-estimating is safer than over). Barge-ins during playback get
cancelled mic audio for STT, but this does not restore full-duplex
confidence the way client AEC does — it's off by default, and mp3
fallback sessions silently get no reference.

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

## Generated media lives on local disk, ephemeral on a free hosting tier

**Decision:** `app/core/storage.py` writes generated images and read-aloud
mp3s under `MEDIA_DIR` and serves them from the app (`/media/files`). No
object store, no media table.

**Alternatives rejected:** media bytes in Postgres (works on the free tier,
but bloats a 1 GB database with blobs and every backup with them); an
S3-compatible bucket (the right production answer, but credentials and a
bucket to own for a template whose deploy target is a demo).

**Consequence:** on a free hosting tier (no persistent disk) media vanishes on
every redeploy and restart, while message rows keep their `media_url` —
old images in chat history 404. Acceptable for a demo; before relying on a
deploy, swap `storage.py` for a bucket (it's the only file that knows where
bytes live) or attach a disk on a paid plan.

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
