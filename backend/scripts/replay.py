"""Re-check a recorded run's answer with the current evidence checker.

Run via `make replay run=<id>` (inside the backend container). Reads
``<DEBUG_RUN_DIR>/<id>.jsonl``, checks its structured answer again with
today's ``evidence`` and ``diagram`` (``app.features.debug.replay``) and
prints each claim whose label or downgrade reason changed against the
result the run sent, and the problems and diagrams that changed. No model
call. Exits 2 when the record can't be replayed (a run that failed or
was cut off before its result), else 0, changed or not.
"""

import argparse
import sys
from pathlib import Path

# The script lives in backend/scripts/ — put the backend dir on sys.path so
# `import app.*` works regardless of the invocation cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.features.debug import record, replay  # noqa: E402


def report(run_id: str, r: replay.Replay) -> str:
    """The comparison as plain text, one change per line."""
    lines = [f"run {run_id}: {len(r.result.hypotheses)} hypotheses"]
    if r.unchanged:
        lines.append("no change: the current checker gives the recorded result.")
        return "\n".join(lines)
    for c in r.claims:
        lines += [f"{c.where}: {c.text}", f"  was {c.before}", f"  now {c.after}"]
    lines += [f"problem added: {p}" for p in r.problems_added]
    lines += [f"problem gone: {p}" for p in r.problems_removed]
    lines += [f"diagram changed: {h}" for h in r.diagrams_changed]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="the run id, e.g. 20261112-021544-b5be8a")
    ap.add_argument("--dir", type=Path, default=Path(settings.DEBUG_RUN_DIR))
    args = ap.parse_args()
    if not record.RUN_ID.fullmatch(args.run):
        print(f"not a run id: {args.run!r}", file=sys.stderr)
        return 2
    path = args.dir / f"{args.run}.jsonl"
    if not path.is_file():
        print(f"no record at {path}", file=sys.stderr)
        return 2
    try:
        r = replay.replay(record.load(path))
    except ValueError as e:
        print(f"can't replay {args.run}: {e}", file=sys.stderr)
        return 2
    print(report(args.run, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
