# docs/

Process artifacts, not code documentation. Architectural trade-offs still
belong in `DECISIONS.md`, and code changes still don't get doc files — see
AGENTS.md. What lives here:

- `architecture/` — design notes on the system as built (or as intended).
  `api/` holds the generated route table (`make docs`) — regenerate,
  don't hand-edit.
- `operations/` — runbooks: how to operate, debug, and recover the app.
  Start at `operations/onboarding.md`.
- `plans/` — implementation plans for work in flight.
- `postmortems/` — what broke, why, and what changed because of it.

## Numbering

`plans/` and `postmortems/` hold numbered files inside range dirs:
`plans/001-100/007-add-auth.md`. Numbers are monotonic within a section
and never reused — even if the doc is later deleted. When a range fills,
add the next one (`101-200`).

`architecture/` and `operations/` are unnumbered — filenames are plain
slugs (`operations/db-restore.md`).
