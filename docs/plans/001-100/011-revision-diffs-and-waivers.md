# Plan 011: Revision diffs and waivers

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-14 | 2026-11-15 | in progress | — | `010-ratings-sequencing-esd.md` (deferred the review workflow here), `012-benchmark-scorecard.md` (goes first; its scorecard measures this plan), `007-one-review-path-recorded.md` (run records), `DECISIONS.md` ("No database", "Runs are recorded as files", "Review mode reuses the debug pipeline") |

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
| 1 | Every finding has a stable key, recorded in the run | done |
| 2 | A review against an earlier run shows new, fixed and unchanged issues | done |
| 3 | Issues match and fingerprint on the pins they cite, not whole parts | proposed |
| 4 | Waivers: an accepted finding stays quiet until its circuit changes | proposed |

## 1. Every finding has a stable key, recorded in the run: done

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
Decided (2026-11-14): ground nets and nets with more than 8 pins count
in a fingerprint by name only, so a part added anywhere on GND or a
rail doesn't change every part on it. Other nets count by their other
pins (refs and values), never by name: KiCad's auto names
(`Net-(C12-Pad1)`) change on renumbering.
Decided (2026-11-14), default: diffs and waivers are applied after the
evidence check, for display only. The model never sees the review file,
so its answer, the prompts and the evals don't change, and a replay of
an old run gives the same issues.
Decided (2026-11-14), default: a calculation's key is its kind and the
pins of its own part (`pin_voltage:U3.7`, `divider:U1.3`), or its part
alone for `part_rating` and `connector_esd`, whose pins list what the
check found. A pin warning's key is `pin_convention:<ref>`. A model
issue's parts come from its pin cites and the calculations it cites;
nets, ERC entries and datasheet facts don't count. `keys.build` writes
them as a `keys` line in the run record, just before the result event;
nothing reads them back yet.

**Checks:** `tests/test_debug_keys.py` (7 tests): reordered parts and
nets, and a renamed auto-named net, give the same keys and fingerprints;
a changed value changes its part's fingerprint, not the key; a changed
neighbour on a small net changes the fingerprint, a far part on GND
doesn't; a 9-pin net counts by name, an 8-pin one by its pins. The
service's record test reads the `keys` line back (`wiring:U2.9`,
`issue:U2`). `make replay` on the 6 recorded runs: no change in claims
or problems. Keys computed from those records: no two findings in a run
share a key; the two runs of one board give the same issue keys, and two
later runs of another board give `issue:J10,R18,U1,U6,U7` and
`issue:R18,U1,U6,U7` (Jaccard 0.8). `make lint` and `make test` passed
(494 backend, 55 frontend), each sub-target run on its own (make 3.81
has no `--output-sync`). Bugs found: one recorded issue's verified
cites name no part, so its key is `issue:` alone; step 2 must leave such
an issue unmatched, never match two empty sets.

## 2. A review against an earlier run shows new, fixed and unchanged issues: done

- The upload names an earlier run; the result marks each issue new or
  unchanged, and lists the earlier run's issues that are gone.

Decided (2026-11-14): the baseline is the same project file as the
waivers (step 4). `<board>.review.json` holds the last review's findings
(key, fingerprint, title, severity, run id, date) as well as the
waivers. Uploading it gives the diff its base, and saving it after a
review moves the base on. It works on any machine, survives deleted run
records, and a git diff of it shows what a review changed. Rejected: a
recorded run id (depends on the server keeping that record, and doesn't
travel to a teammate); uploading both revisions (two full reviews per
diff, and the model's run-to-run variation shows up as fake changes).
Decided (2026-11-14): only the model's issues are marked new or
unchanged and listed as fixed. The review file still records every keyed
finding (calculations, wiring findings, pin warnings), so step 4 can
waive those too. Rejected: marking wiring findings and pin warnings, or
every calculation (most calculations are checks, not problems).
Decided (2026-11-14): the baseline is a `<project>.review.json` in the
dropped folder, picked up with the schematic with no extra step; the
input view says it was found. The backend computes the diff and sends it
with the result. Rejected: a separate file picker (one more step every
revision); a diff in the browser only (logic in TypeScript, and the eval
pair can't check it).
Decided (2026-11-14): an issue that matches an earlier one while a part
it cites has a changed fingerprint is marked unchanged, with a "circuit
changed" note naming those parts. Rejected: a fourth "changed" mark;
treating it as new (a value tweak that didn't fix it would look like a
new problem).
Decided (2026-11-14), default: review mode only; a debug run ignores the
review file. Each model issue matches at most one earlier issue, highest
Jaccard first (≥ 0.5 over cited parts), labelled "likely the same". An
issue whose key names no parts (`issue:`) is never matched: a new one
gets no mark, and an old one isn't listed as fixed.
Decided (2026-11-14), default: only `<project>.review.json` (the
`.kicad_pro` stem) is the baseline; other `*.review.json` files are
ignored. It is a Pydantic `ReviewFile`: `version`, `project`, `run_id`,
`date`, `findings` (key, source, parts, title, severity) and `waivers`
(empty until step 4). The backend sends the next review file with the
result, and a "Save review file" button downloads it. A malformed file,
or one with an unknown `version`, gives a review with no baseline and a
note saying why, not an error. The logic goes in
`backend/app/features/debug/review_file.py`.

**Checks:** `tests/test_debug_review_file.py` (11 tests): no file, or
another project's file, is no baseline; a malformed file or another
`version` is no baseline with a note; one fixed, one new and one
unchanged; a match with a changed part names it; overlap below 0.5 is
no match; each earlier issue matches once, best overlap first; issues
naming no part are never matched; the next file keeps every keyed
finding and the waivers. The service's review test reads the
`review_diff` event and the record line. The eval pair failed: rev A =
`orbit-boot0-high`, rev B = `orbit-uart-swapped` (the same board with
BOOT0 fixed and the UART swapped), keyed from the 3 stored scorecard
reviews of each (`20261114T065544Z`), so 9 A→B pairs. Exactly one fixed
and one new: 1 of 9 pairs, and there the wrong ones. BOOT0 fixed and
the UART swap new: 4 of 9, each with extra new and fixed issues. Why:
an issue citing many parts matches a different one (B's "slide-switch
common" cites BAT1, BOOT0, PWR1, R2, U16 and matches A's BOOT0 issue,
BOOT0, R2, U16, at 0.6, so BOOT0 isn't fixed); two issues on the same
two ICs match (A's "MCU drives unpowered CP2102", U16 and U22, matches
B's UART swap at 1.0, so the swap is unchanged); one issue cites
different parts run to run (ESD: J1, J2, U16, U22, USB1 vs U22, USB1,
0.4), so it shows as new plus fixed. "Circuit changed" is too coarse:
both planted edits touch U16's and U22's nets, so every match citing
them, the HSE caps and ESD included, says "circuit changed: U16", and a
step 4 waiver on any of them would lapse. Matching and fingerprints on
cited pins, not whole parts, would separate these: step 3. `make lint` and
`make test` passed (509 backend, 56 frontend), each sub-target run on
its own. Bugs found: `lint-tsc` caught two `Run` literals in
`present.test.ts` missing the new `reviewDiff`.

## 3. Issues match and fingerprint on the pins they cite, not whole parts: proposed

- A model issue's key is the set of pins its verified cites name
  (`U16.7`, `U22.25`), not their parts, so two issues on the same two
  ICs but different pins don't match.
- An issue's fingerprint covers only the circuit at those pins, so an
  edit on another pin of the same IC doesn't say "circuit changed".
- The review file's findings carry these keys and fingerprints
  (`ReviewFile` `version` 2; a version 1 file gives the
  `unknown_version` note).

Added (2026-11-15) after step 2's eval pair failed: matching on whole
parts matched different issues on the same ICs, and both planted edits
changed U16's and U22's fingerprints, so a waiver on any issue citing
them would lapse.

**Checks:** step 2's eval pair (`orbit-boot0-high` → `orbit-uart-swapped`,
the 9 pairs from the stored scorecard reviews): more pairs show exactly
BOOT0 fixed and the UART swap new than step 2's 1 of 9, and no match
outside the edited pins says "circuit changed". Unit tests for pin keys
and pin fingerprints.

## 4. Waivers: an accepted finding stays quiet until its circuit changes: proposed

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
