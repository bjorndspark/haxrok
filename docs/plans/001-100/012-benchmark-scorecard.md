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
| 2 | Each issue a review raises is judged real or wrong, and the judgement is kept | done |
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

## 2. Each issue a review raises is judged real or wrong, and the judgement is kept: done

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

Decided (2026-11-14): `evals/judged.py` parses `judged.md` (one
`## <title>` entry per issue with `Verdict: real|wrong [(draft)]`,
`Cites:`, `Match:` and `Why:`) and judges each issue. The `Auto-check:`
match comes first as the planted bug; otherwise the first entry whose
`Cites` (the parts, pins and nets the claims cite, compared whole) and
`Match` (keywords in the title, summary and claims) both hold wins.
Otherwise the issue is unjudged. A malformed entry raises an error
naming its line, so a typo can't drop a judgement. `app_prompts.py`'s
`match_rank()` uses the same keyword matcher. The scorecard gains a
"Judged per run (bug/real/wrong/unjudged)" column, with a draft count
shown as `3+2d`, and writes `unjudged.md` listing each unjudged issue
with its text and cites to judge next. `make eval-score from=<folder>`
rescores a saved sweep from its analysis JSONs after a `judged.md`
edit, with no model call.

Decided (2026-11-14): the cases sharing a board (four orbit, three
rp-micro, four rust-board) each repeat the same entries, as the
per-case `judged.md` decision has it. The first drafts cover 18 issues:
real (draft) are the USB lines with no ESD array (orbit, rp-micro), the
rust-board's GPIO2 strap with no pull-up, its buck dropping out at low
battery, its two Schottkys in series, its 32k crystal with DNP load
caps, the furby's floating PS/SYNC, the orbit's PWR switch cutting the
charger off, its PWR switch possibly using pad 2 as the common, and its
UART back-powering the unpowered side. Wrong (draft) are the orbit's L1
"fitted as 2.2uH" (`DFE201612E-2R2M` is the footprint's name; the
value is 0.47uH), its 30pF HSE caps, the rp-micro's 20pF crystal caps
(Y1's CL is unknown), its SB1/SB2 "shorts", the furby's "unwired" mic
(it is off-board, on solder-wire pads), its GPIO3 strap (unused without
the eFuse), the rust-board's GPIO8 strap (R17 pulls it up) and its
ENABLE pin "into a 3.3V chip" (U4 is the edge header). The PWR switch
common and PS/SYNC need a datasheet look when confirming.

**Checks:** a rerun of a judged case scores without new judging, except
for issues it hasn't raised before. The step 1 sweep
(`20261113T175417Z`, 12 cases × 3, 3 runs failed on the gateway's 503)
rescored with the drafts: 99 issues, real 39d, wrong 25d, unjudged 0.
Every match was listed and read: each of the 64 issues matched the
right entry. `make eval-score n=2
cases=rust-board-usb-no-esd,orbit-boot0-high` (`20261113T184710Z`):
13 issues, 0 unjudged; 12 matched the right entry, and one new issue
(ENABLE pull-up) matched the buck entry, below. A scratch test of
`judged.py` passed: bad entries raise, a draft wrong entry matches, the
bug wins over an entry, and an unmatched issue is unjudged. `make lint`
passed (1 old warning in `shared/ui/button.tsx`) and `make test` passed
(487 backend, 55 frontend); `judged.py`, `scorecard.py` and
`app_prompts.py` pass ruff check and mypy. Bugs found: the buck entry's
`+5v` cite and `drop` keyword were broad enough to catch the rerun's new
ENABLE issue, so an issue it had never raised came out judged rather
than unjudged. The entry now needs `buck` and a dropout word, and the
ENABLE issue has its own entry. A loose entry hides a new issue, so
each rerun's matches should be read, not just the unjudged count. The
sweep process had loaded the old `scorecard.py`; `from=` rescored it.

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
