# Plan 012: A benchmark scorecard

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-14 | 2026-11-14 | in progress | — | `evals/README.md` (cases, `make eval-app`), `001-mvp-scope.md` (review mode), `011-revision-diffs-and-waivers.md` (stable finding keys; this plan goes first) |

Plans 002–010 added checks one at a time, and each was judged on the
eval case it was built for: was the planted bug ranked, and did
`rp-micro-no-fault` stay quiet. Nobody can answer the two questions a
paying team asks first:

- **How many real bugs does it miss?** Eleven of the twelve cases are
  planted: one clean edit on an otherwise good board, invisible to ERC.
  Only `furby-boot-cap` is a bug someone actually shipped. Planted
  scores are not accuracy on real boards (`evals/README.md` says so).
- **How much of what it says is wrong?** A review returns 10–20 issues.
  Only the planted bug is scored; the rest are read by eye, once, and
  forgotten. The only false-alarm test is one clean board, and its pass
  has flipped between runs (`evals/README.md`, "Through the backend from
  KiCad files").

The product direction is to sell to small KiCad teams first and earn the
evidence a large company would ask for: a measured miss rate and a
measured false-alarm rate on real bugs. Every later plan, 011 included,
should move those numbers, not only pass its own case.

The goal: one command runs every case in review mode several times and
prints a scorecard: per case, the rank the real bug got in each run, and
per board, how many issues were wrong. More clean boards measure false
alarms; more real bugs from public fix commits measure misses.

Acceptance:

- One `make` target runs the scorecard over all cases and writes a
  table that later plans can be compared against.
- At least three more unmodified boards and three more real-bug cases
  from public repos, each with a source commit and license.
- A baseline recorded in `evals/README.md`: miss rate on planted and on
  real cases separately, and issues judged wrong per board.

Effort: about 2–3 days, a step per session.

| Step | What | |
|---|---|---|
| 1 | One command scores every case over several runs | done |
| 2 | Each issue a review raises is judged real or wrong, and the judgement is kept | proposed |
| 3 | More unmodified boards measure false alarms | proposed |
| 4 | Real bugs from public fix commits measure misses | proposed |
| 5 | A baseline scorecard recorded | proposed |

## 1. One command scores every case over several runs: done

- A script under `evals/` that runs `app_prompts.py`'s review mode on
  every case (or a list), N times, at most ~3 at once, and collects each
  run's structured analysis.
- Per case and run: the rank where the `Auto-check:` keywords match, and
  the issues by severity (won't work, may fail, improvement).
- Writes a markdown scorecard and a JSON file under `evals/results/`
  (gitignored), so two scorecards can be diffed.
- Named `evals/scorecard.py`, run as `make eval-score [n=3]
  [cases=a,b]`, beside `make eval-app`.

Decided (2026-11-14): N = 3 runs per case. It shows run-to-run flips
(like `rp-micro-no-fault`'s) and a full sweep of ~18 cases takes about
an hour at 3 at once.

Decided (2026-11-14): the reviews bypass the AI answer cache. The first
check run gave `rust-board-usb-no-esd` the same cached answer twice
(0 s each), so N runs were one answer counted N times. The script reads
every case's datasheets first, through the cache as the app does (the
same facts each run), then sets `AI_CACHE_TTL_S = 0` in its own process
for the reviews.

Decided (2026-11-14): `app_prompts.py`'s two prompt calls moved into
`answer()` and its keyword rank into `match_rank()`, which the scorecard
calls, so both scripts send the same review. The diagnosis is still
written as soon as it arrives, so a failed structure call leaves it to
read. A case with `Auto-check: none` gets no rank; its issues count as
candidate false alarms until step 2 judges them. Results go to
`results/scorecard/<model>/<UTC time>/`, with each run's diagnosis and
analysis beside `scorecard.md` and `scorecard.json`.

**Checks:** `make eval-score n=2
cases=rust-board-usb-no-esd,rp-micro-no-fault`: ESD bug ranked #2 and
#4 (0/2/0 and 0/5/0 issues as will fail/may fail/improve); the clean
board raised 0 issues, then 3 (1 will fail: SB1/SB2 "shorts" GPIO10/11),
which is the flip this plan is for. Ranks and counts recounted from each
run's analysis JSON by a separate script: all four match. Runs took
90–238 s. `make eval-app case=rust-board-usb-no-esd mode=review` still
runs through the refactored `answer()`. `make lint` passed and
`make test` passed (487 backend, 55 frontend); `scorecard.py` and
`app_prompts.py` also pass ruff check and mypy (they sit outside
`make lint`'s scope). Bugs found: the cached answers above; the check's
`|` alternatives split the markdown table (escaped now); the result folder's
time was UTC with no marker (now `…T…Z`). No unit test: `evals/` has no
test setup, and the check above compares the table to the raw outputs.

## 2. Each issue a review raises is judged real or wrong, and the judgement is kept: proposed

A review's other issues are not all false alarms: the planted boards are
real open-source boards with their own real flaws. Precision needs each
issue judged once, and the judgement reused on the next run, since the
model words the same issue differently each time.

Decided (2026-11-14): each case keeps a `judged.md`: known real issues
and known false alarms (correct parts a reviewer might flag), each
matched by the parts, pins or nets it cites plus keywords, the way
`Auto-check:` lines work. Code does the matching; an issue that matches
no entry is shown as unjudged, drafted by the agent and confirmed by the
user before it's added. Rejected: an LLM judge (the judgement itself
varies between runs, and it's weak evidence for a large company), and
counting severities only (a planted board's own real flaws would count
as false alarms).
Decided (2026-11-14): steps 1–4 run unattended in `/step-loop`. The
agent's judgements go into `judged.md` marked `draft`, and the scorecard
counts a draft entry apart from a confirmed one. The user confirms the
drafts in the loop's whole-branch review; step 5 runs only after that.

**Checks:** a rerun of a judged case scores without new judging, except
for issues it hasn't raised before.

## 3. More unmodified boards measure false alarms: proposed

- At least three open-source KiCad boards, unmodified, with permissive
  licenses, of different kinds (an MCU board, a power board, a sensor or
  interface board), exported with KiCad 9 as `evals/README.md` says.
- Review mode only; `expected.md` says there's no planted fault and
  lists what a reviewer might wrongly flag.

**Checks:** each case's ERC and netlist export; one scorecard run.

## 4. Real bugs from public fix commits measure misses: proposed

- At least three cases where a public KiCad repo's commit fixes a
  schematic bug: the board before the fix is the case, the fix is
  `expected.md`. Symptoms come from the commit message, issue or
  changelog only, never from the diff.
- Same export and license rules as the planted cases.

Decided (2026-11-14): real bugs come only from public repos' fix
commits (a revision note, a fix linked to an issue), permissive
licenses only, so every case can be committed. The user's own private
boards stay out of this plan.
Decided (2026-11-14): in steps 3 and 4 the agent picks the boards and
commits itself, by these rules: a permissive license, KiCad sources that
export with KiCad 9, kinds that differ from the existing cases, and (step
4) a fix whose cause is stated in the commit, issue or changelog, not
guessed from the diff. Each choice and the candidates passed over are
written in the step's section, and the user reviews them with the drafts.

**Checks:** each case's ERC and netlist export; one scorecard run.

## 5. A baseline scorecard recorded: proposed

- Runs only after the user has confirmed the `draft` judgements and the
  boards picked in steps 2–4.
- Run the full scorecard and record it in `evals/README.md`: misses on
  planted and on real cases separately, and wrong issues per board.
- The `/step` **Check** for prompt changes points at the scorecard.

**Checks:** the numbers in `evals/README.md` match the scorecard's JSON.
