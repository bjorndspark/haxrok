# Plan 011: Revision diffs and waivers

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-14 | 2026-11-14 | proposed | — | `010-ratings-sequencing-esd.md` (deferred the review workflow here), `012-benchmark-scorecard.md` (goes first; its scorecard measures this plan), `007-one-review-path-recorded.md` (run records), `DECISIONS.md` ("No database", "Runs are recorded as files", "Review mode reuses the debug pipeline") |

A review today is a single shot: upload a schematic and get back a list of
issues. Real boards go through several revisions, and on each one a
reviewer needs answers to two questions the app can't give yet:

- **What changed since the last review?** Which issues are new, which
  were fixed, and which are still there. Without this, every revision
  is a fresh list of 10–20 items, and the user has to diff it by eye.
- **What has already been accepted?** A team that has looked at a
  finding and decided it's fine (the 6.3 V capacitor on a rail that never
  sees USB, a header that never leaves the box, so no ESD) needs it to
  stay quiet on the next revision. It should come back only when the
  circuit it was about changes. This is what KiCad's ERC exclusions do
  for ERC. Teams expect the same from a reviewer, and the product
  direction needs it: findings kept as review records.

Two constraints shape it:

- **No database, no auth** (`DECISIONS.md`). Runs are already recorded
  as files (`data/runs/<id>.jsonl`), but nothing on the server knows
  who a user is or which runs belong to one project.
- **Only some findings have an identity.** Backend checks (`Calculation`:
  kind, part, pins; `WiringFinding`: pin, net) are the same on two
  revisions when the circuit is. The model's issues are free text: two
  runs on the same schematic can word, split or rank the same problem
  differently. Their cites (pins, nets, calculation ids) are the only
  stable handle.

| Step | What | |
|---|---|---|
| 1 | Every finding has a stable key, recorded in the run | proposed |
| 2 | A review against an earlier run shows new, fixed and unchanged issues | proposed |
| 3 | Waivers: an accepted finding stays quiet until its circuit changes | proposed |

## 1. Every finding has a stable key, recorded in the run: proposed

- A key for each backend finding from what it is about (kind, part,
  pins, nets), not from its id (`c3`, `wf1` are list positions).
- A key for each model issue from its verified cites.
- A fingerprint of the circuit the finding is about (the values and
  connections of the parts it names), so a later step can tell
  "same finding, same circuit" from "same finding, circuit changed".

Decided (2026-11-14): a backend finding's key is exact: its kind plus
the parts, pins and nets it is about (`part_rating:C12`,
`connector_esd:J3`, `wiring:U2.9`). A model issue's key is the set of
parts its verified cites name (downgraded cites don't count), and two
model issues match when those sets mostly overlap (Jaccard ≥ 0.5, tuned
on the eval pair). The UI marks such a match "likely the same", never
"same". A waiver on a model issue lapses when any cited part's circuit
changes. Rejected: backend findings only (the model's false alarms are
most of the noise a team wants to waive); asking the model to match (a
prompt and schema change, tokens, and matches that vary run to run).
Decided (2026-11-14), default: a part's fingerprint is a hash of its
value, footprint and, for each of its pins, the net and that net's other
pins (refs and values). It changes when the part or anything wired to it
changes, and not when a far-off part moves or is renumbered.
Decided (2026-11-14), default: diffs and waivers are applied after the
evidence check, for display only. The model never sees the review file,
so its answer, the prompts and the evals don't change, and a replay of
an old run gives the same issues.

**Checks:** unit tests: the same netlist with parts reordered or renamed
nets elsewhere gives the same keys; a changed value on a cited part
changes the fingerprint, not the key. `make replay` on recorded runs shows
no change in claims or problems.

## 2. A review against an earlier run shows new, fixed and unchanged issues: proposed

- The upload names an earlier run; the result marks each issue new or
  unchanged, and lists the earlier run's issues that are gone.

Decided (2026-11-14): the baseline is the same project file as the
waivers (step 3). `<board>.review.json` holds the last review's findings
(key, fingerprint, title, severity, run id, date) as well as the
waivers. Uploading it gives the diff its base, and saving it after a
review moves the base on. It works on any machine, survives deleted run
records, and a git diff of it shows what a review changed. Rejected: a
recorded run id (depends on the server keeping that record, and doesn't
travel to a teammate); uploading both revisions (two full reviews per
diff, and the model's run-to-run variation shows up as fake changes).

**Checks:** an eval case pair (rev A, rev B with one fault fixed and one
added): the diff shows exactly one fixed and one new. Unit tests for the
matching.

## 3. Waivers: an accepted finding stays quiet until its circuit changes: proposed

- The user marks an issue accepted, with a reason; later reviews show it
  folded away with that reason, and bring it back when its fingerprint
  changes.

Decided (2026-11-14): waivers live in a file in the user's project, next
to the `.kicad_sch`: `<board>.review.json` (a neutral name, since the app
name is config), downloaded from the UI and uploaded with the schematic.
The team shares it through git and reviews it in PRs, like KiCad's ERC
exclusions. The server keeps no waiver state, so "No database" and "No
auth" hold. Each waiver holds the finding's key, the fingerprint it was
accepted at, a reason, who and when.
Decided (2026-11-14), default: with no auth, "who" is a name the user
types once, kept in the browser (localStorage) as a convenience. The
file in git is the real record of who accepted what.

**Checks:** unit tests for apply and lapse; the eval pair with a waiver
on one finding shows it folded on rev B, and back after its part's value
changes.
