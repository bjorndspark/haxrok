"""Score every eval case in review mode over several runs (plan 012).

Each case goes through the app's review prompts (``app_prompts.answer``,
the same calls as ``make eval-app mode=review``) N times. Per run it
records the rank where the case's ``Auto-check:`` keywords first match
(gonogo.py's rule) and the issues raised, by severity, each judged against
the case's ``judged.md`` (``judged.py``): the planted bug, real, wrong, or
unjudged. A case without a check (an unmodified board) has no bug to rank.

Runs inside the backend container, which mounts evals/ at /evals:
  make eval-score [n=3] [cases=rp-micro-no-fault,furby-boot-cap]

Writes evals/results/scorecard/<model>/<time>/: scorecard.md (the table),
scorecard.json (the same, for diffing two scorecards) and each run's
<case>/run-<i>-diagnosis.md and run-<i>-analysis.json; <time> is UTC.
unjudged.md lists the issues no judged.md entry matched, to judge next.

After editing a judged.md (or an Auto-check line), rescore a saved sweep
without calling the model; it rewrites that folder's scorecard and
unjudged.md:
  make eval-score from=evals/results/scorecard/<model>/<time>

Every case's datasheets are read first, through the AI answer cache as the
app reads them, so each run gets the same facts. Then the cache is turned
off (``AI_CACHE_TTL_S = 0``, this process only) for the reviews: with it
on, every run after the first gets the first run's answer back, and the
scorecard would count one answer N times. The gateway can't take much
concurrency, so at most ``--jobs`` calls (default 3) are in flight at
once. A run that fails (a timeout, a schema error) is recorded with its
error and the sweep goes on.
"""

import argparse
import asyncio
import json
import pathlib
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any

# app_prompts puts the backend (/app) on sys.path, so `app.*` imports below.
from app_prompts import ROOT, answer, ic_parts, parse_compact, read_datasheets
from gonogo import auto_check
from judged import BUG, UNJUDGED, Entry, Groups, hypothesis_cites, judge, load

from app.core.clock import get_clock  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.features.debug.datasheet import HttpFetcher  # noqa: E402
from app.features.debug.facts import DatasheetReading  # noqa: E402

SEVERITIES = ("will_fail", "may_fail", "improve")
# Judgement columns, in table order; real and wrong count drafts apart.
JUDGED = (BUG, "real", "wrong", UNJUDGED)


@dataclass
class Issue:
    """One issue of a run and its judgement: ``verdict`` is one of
    ``JUDGED``, ``entry`` the judged.md entry that matched."""

    rank: int
    severity: str | None
    title: str
    verdict: str = UNJUDGED
    draft: bool = False
    entry: str | None = None


@dataclass
class Run:
    """One review of a case. ``rank`` is None when the check missed (or the
    case has no check); ``error`` is set when the run failed."""

    run: int
    rank: int | None = None
    issues: list[Issue] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    def counts(self) -> dict[str, int]:
        """Issues per severity; a missing severity counts as ``none``."""
        out = dict.fromkeys(SEVERITIES, 0)
        for i in self.issues:
            key = i.severity or "none"
            out[key] = out.get(key, 0) + 1
        return out

    def judged(self, draft: bool) -> dict[str, int]:
        """Issues per judgement, counting only confirmed (``draft`` False)
        or only draft entries; bugs and unjudged issues count as confirmed."""
        out = dict.fromkeys(JUDGED, 0)
        for i in self.issues:
            if i.draft == draft:
                out[i.verdict] += 1
        return out


@dataclass
class Case:
    name: str
    check: str | None  # the Auto-check line, None for an unmodified board
    runs: list[Run] = field(default_factory=list)


def check_text(groups: Groups | None) -> str | None:
    """The Auto-check groups written back as expected.md spells them."""
    return None if groups is None else "; ".join("|".join(g) for g in groups)


def rank_cell(case: Case) -> str:
    """Per-run ranks: ``#1 #3 miss``; ``err`` for a failed run, ``-`` for a
    case with no check."""
    cells = []
    for r in case.runs:
        if r.error:
            cells.append("err")
        elif case.check is None:
            cells.append("-")
        else:
            cells.append(f"#{r.rank}" if r.rank is not None else "miss")
    return " ".join(cells)


def issues_cell(case: Case) -> str:
    """Per-run issue counts as will_fail/may_fail/improve, e.g. ``1/4/6 0/3/5``."""
    return " ".join(
        "err" if r.error else "/".join(str(r.counts()[s]) for s in SEVERITIES) for r in case.runs
    )


def count_text(confirmed: int, draft: int) -> str:
    """A count with its drafts apart: ``3``, ``3+2d`` or ``2d``."""
    if not draft:
        return str(confirmed)
    return f"{confirmed}+{draft}d" if confirmed else f"{draft}d"


def judged_cell(case: Case) -> str:
    """Per-run judgements as bug/real/wrong/unjudged, e.g. ``1/3+2d/1d/4``."""
    cells = []
    for r in case.runs:
        if r.error:
            cells.append("err")
            continue
        confirmed, draft = r.judged(False), r.judged(True)
        cells.append("/".join(count_text(confirmed[k], draft[k]) for k in JUDGED))
    return " ".join(cells)


def summary(cases: list[Case]) -> dict[str, int]:
    """Totals over runs that finished: for cases with a check, how often the
    bug ranked first, ranked at all, or was missed; for cases without one,
    the issues raised; over all, the issues judged real, wrong (confirmed
    and draft apart) and not yet judged."""
    checked = [r for c in cases if c.check is not None for r in c.runs if not r.error]
    clean = [r for c in cases if c.check is None for r in c.runs if not r.error]
    done = [r for c in cases for r in c.runs if not r.error]

    def total(verdict: str, draft: bool) -> int:
        return sum(r.judged(draft)[verdict] for r in done)

    return {
        "checked_runs": len(checked),
        "first": sum(r.rank == 1 for r in checked),
        "found": sum(r.rank is not None for r in checked),
        "missed": sum(r.rank is None for r in checked),
        "clean_runs": len(clean),
        "clean_issues": sum(len(r.issues) for r in clean),
        "clean_will_fail": sum(r.counts()["will_fail"] for r in clean),
        "runs": len(done),
        "issues": sum(len(r.issues) for r in done),
        "real": total("real", False),
        "real_draft": total("real", True),
        "wrong": total("wrong", False),
        "wrong_draft": total("wrong", True),
        "unjudged": total(UNJUDGED, False),
        "failed_runs": sum(bool(r.error) for c in cases for r in c.runs),
    }


def markdown(cases: list[Case], meta: dict[str, object]) -> str:
    """The scorecard as a markdown table plus its totals."""
    s = summary(cases)
    lines = [
        f"# Scorecard {meta['time']}",
        "",
        f"Model `{meta['model']}`, review mode, {meta['n']} runs per case.",
        "",
        "| Case | Check | Rank per run | Issues per run (will fail/may fail/improve) "
        "| Judged per run (bug/real/wrong/unjudged) |",
        "|---|---|---|---|---|",
    ]
    for c in cases:
        # the check's "|" alternatives would split the table's cells
        check = (c.check or "none").replace("|", "\\|")
        lines.append(
            f"| {c.name} | {check} | {rank_cell(c)} | {issues_cell(c)} | {judged_cell(c)} |"
        )
    lines += [
        "",
        f"- Cases with a check: {s['first']}/{s['checked_runs']} runs ranked the bug "
        f"first, {s['found']} ranked it at all, {s['missed']} missed it.",
        f"- Cases without one: {s['clean_issues']} issues over {s['clean_runs']} runs "
        f"({s['clean_will_fail']} will fail).",
        f"- All cases: {s['issues']} issues over {s['runs']} runs; real "
        f"{count_text(s['real'], s['real_draft'])}, wrong "
        f"{count_text(s['wrong'], s['wrong_draft'])}, unjudged {s['unjudged']} "
        "(`d`: judged by a draft entry, not yet confirmed).",
        f"- Failed runs: {s['failed_runs']}.",
        "",
    ]
    return "\n".join(lines)


def score_run(
    i: int, analysis: dict[str, Any], groups: Groups | None, entries: list[Entry]
) -> Run:
    """Run ``i`` scored from its analysis (``Analysis.model_dump()`` or its
    saved JSON): each issue judged, and the rank of the first one judged
    the planted bug (``app_prompts.match_rank``'s rule)."""
    run = Run(i)
    for h in analysis["hypotheses"]:
        j = judge(h, groups, entries)
        run.issues.append(Issue(h["rank"], h["severity"], h["title"], j.verdict, j.draft, j.entry))
    run.rank = next((x.rank for x in run.issues if x.verdict == BUG), None)
    return run


def unjudged(cases: list[Case], out_dir: pathlib.Path) -> str:
    """The issues no judged.md entry matched, per case, with what an entry
    is written from: the run, severity, summary and cited parts, pins, nets."""
    lines = ["# Unjudged issues", ""]
    for c in cases:
        found = [(r, i) for r in c.runs for i in r.issues if i.verdict == UNJUDGED]
        if not found:
            continue
        lines += [f"## {c.name}", ""]
        for r, i in found:
            path = out_dir / c.name / f"run-{r.run}-analysis.json"
            h = next(h for h in json.loads(path.read_text())["hypotheses"] if h["rank"] == i.rank)
            cites = ", ".join(sorted(hypothesis_cites(h))) or "none"
            lines += [
                f"- run {r.run} #{i.rank} ({i.severity}) **{i.title}**",
                f"  {h['summary']}",
                f"  Cites: {cites}",
            ]
        lines.append("")
    if len(lines) == 2:
        lines.append("None: every issue matched the Auto-check or a judged.md entry.")
    return "\n".join(lines) + "\n"


def write(cases: list[Case], meta: dict[str, object], out_dir: pathlib.Path) -> None:
    """scorecard.md, scorecard.json and unjudged.md into ``out_dir``."""
    (out_dir / "scorecard.md").write_text(markdown(cases, meta))
    (out_dir / "scorecard.json").write_text(
        json.dumps(
            {**meta, "summary": summary(cases), "cases": [asdict(c) for c in cases]}, indent=1
        )
        + "\n"
    )
    (out_dir / "unjudged.md").write_text(unjudged(cases, out_dir))


def rescore(out_dir: pathlib.Path) -> None:
    """Score a saved sweep again from its analysis files, with the cases'
    current Auto-check lines and judged.md, and rewrite its scorecard. A run
    that failed stays failed; no model is called."""
    saved = json.loads((out_dir / "scorecard.json").read_text())
    cases = []
    for c in saved["cases"]:
        case_dir = ROOT / "cases" / c["name"]
        groups, entries = auto_check(case_dir), load(case_dir)
        case = Case(c["name"], check_text(groups))
        for r in c["runs"]:
            if r["error"]:
                case.runs.append(Run(r["run"], seconds=r["seconds"], error=r["error"]))
                continue
            path = out_dir / c["name"] / f"run-{r['run']}-analysis.json"
            run = score_run(r["run"], json.loads(path.read_text()), groups, entries)
            run.seconds = r["seconds"]
            case.runs.append(run)
        cases.append(case)
    meta = {k: saved[k] for k in ("time", "model", "n", "mode")}
    write(cases, meta, out_dir)


async def read_case(name: str, gate: asyncio.Semaphore) -> DatasheetReading:
    """The case's datasheets read as for an upload, under a slot of
    ``gate``: a first read calls the gateway per IC. A failed read is
    printed and the case is reviewed without datasheets."""
    case_dir = ROOT / "cases" / name
    try:
        async with gate:
            design = parse_compact((case_dir / "netlist.txt").read_text())
            _, reading = await read_datasheets(design, ic_parts(case_dir, design), HttpFetcher())
            return reading
    except Exception:
        print(f"{name}: datasheets failed, reviewing without them\n{traceback.format_exc()}")
        return DatasheetReading()


async def review_case(
    name: str,
    reading: DatasheetReading,
    n: int,
    gate: asyncio.Semaphore,
    out_dir: pathlib.Path,
    timeout_s: float,
) -> Case:
    """``n`` reviews of one case, each holding a slot of ``gate``."""
    case_dir = ROOT / "cases" / name
    groups, entries = auto_check(case_dir), load(case_dir)
    case = Case(name, check_text(groups))
    (out_dir / name).mkdir(parents=True, exist_ok=True)

    async def one(i: int) -> Run:
        async with gate:
            start = time.monotonic()
            try:
                result = await answer(
                    case_dir, reading, "review", timeout_s, out_dir / name / f"run-{i}-diagnosis.md"
                )
            except Exception as e:
                print(f"{name} run {i}: failed: {e!r}")
                return Run(i, seconds=round(time.monotonic() - start, 1), error=repr(e))
        a = result.analysis
        (out_dir / name / f"run-{i}-analysis.json").write_text(a.model_dump_json(indent=1))
        run = score_run(i, a.model_dump(), groups, entries)
        run.seconds = round(result.total_s, 1)
        print(f"{name} run {i}: rank {run.rank}  issues {len(run.issues)}  {run.seconds:.0f}s")
        return run

    case.runs = list(await asyncio.gather(*(one(i) for i in range(1, n + 1))))
    return case


async def main_async(names: list[str], n: int, jobs: int, timeout_s: float) -> pathlib.Path:
    stamp = get_clock().now().strftime("%Y%m%dT%H%M%SZ")
    model = settings.AI_CHAT_MODEL
    out_dir = ROOT / "results" / "scorecard" / model.replace("/", "_") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(jobs)
    readings = await asyncio.gather(*(read_case(c, gate) for c in names))
    # From here on every call goes to the model: see the module docstring.
    settings.AI_CACHE_TTL_S = 0
    cases = list(
        await asyncio.gather(
            *(
                review_case(c, r, n, gate, out_dir, timeout_s)
                for c, r in zip(names, readings, strict=True)
            )
        )
    )
    meta: dict[str, object] = {"time": stamp, "model": model, "n": n, "mode": "review"}
    write(cases, meta, out_dir)
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="runs per case")
    ap.add_argument("--cases", default="", help="comma-separated case names; default all")
    ap.add_argument("--jobs", type=int, default=3, help="runs in flight at once")
    ap.add_argument("--timeout", type=float, default=settings.DEBUG_AI_TIMEOUT_S)
    ap.add_argument("--rescore", type=pathlib.Path, help="a saved sweep's folder to score again")
    args = ap.parse_args()
    if args.rescore:
        # Given from the repo root (evals/results/...); evals/ is /evals here.
        out_dir = ROOT.parent / args.rescore
        rescore(out_dir)
        print((out_dir / "scorecard.md").read_text())
        print(f"Rescored {args.rescore}; unjudged issues in unjudged.md")
        return
    known = sorted(p.name for p in (ROOT / "cases").iterdir() if (p / "expected.md").exists())
    names = [c.strip() for c in args.cases.split(",") if c.strip()] or known
    if bad := [c for c in names if c not in known]:
        ap.error(f"--cases: unknown {', '.join(bad)}")
    if args.n < 1 or args.jobs < 1:
        ap.error("--n and --jobs must be at least 1")
    out_dir = asyncio.run(main_async(names, args.n, args.jobs, args.timeout))
    print((out_dir / "scorecard.md").read_text())
    print(f"Written to {out_dir.relative_to(ROOT.parent)}; unjudged issues in unjudged.md")


if __name__ == "__main__":
    main()
