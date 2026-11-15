"""The review file and the diff against it (plan 011 step 2).

``<project>.review.json`` sits next to the ``.kicad_pro`` and holds the last
review's findings (``ReviewFile``). Dropped with the project, it is the
baseline: each model issue of the new review is marked new or unchanged
against it, and the baseline's issues with no match are listed as fixed.
After the review the backend sends the next review file, which the user
saves over the old one, moving the baseline on.

Only model issues are compared. Their key is the set of parts their
verified cites name (``keys``), which two runs word and split differently,
so two issues match when those sets mostly overlap (Jaccard ≥
``MATCH_JACCARD``), each at most once, best overlap first; the UI calls a
match "likely the same". An issue whose cites name no part can't be
matched: a new one gets no mark, an old one isn't listed as fixed.

Applied after the evidence check, for display only: the model never sees
the review file. Pure functions; the service reads the upload and the clock.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import ValidationError

from app.features.debug.project import REVIEW_SUFFIX, Project
from app.features.debug.schemas import (
    Calculation,
    CheckedAnalysis,
    FindingKey,
    IssueMark,
    PinWarning,
    ReviewDiff,
    ReviewFile,
    ReviewFinding,
    WiringFinding,
)

# Two model issues are likely the same when their cited parts overlap this
# much (|A ∩ B| / |A ∪ B|); tuned on the recorded runs (plan 011 step 1).
MATCH_JACCARD = 0.5


@dataclass(frozen=True)
class Baseline:
    """What came of the uploaded review file: the file, or why there is
    none (``note``; None when no review file was sent)."""

    file: ReviewFile | None = None
    note: Literal["unreadable", "unknown_version"] | None = None


def path(project: Project) -> str:
    """Where the project's review file goes: next to its root sheet, named
    after the project."""
    return str(PurePosixPath(project.root).parent / f"{project.name}{REVIEW_SUFFIX}")


def read(project: Project) -> Baseline:
    """The project's review file from the upload. Malformed JSON, or a file
    that isn't a ``ReviewFile``, is ``unreadable``; one with another
    ``version`` is ``unknown_version``. Neither is an error: the review just
    has no baseline."""
    data = project.files.get(path(project))
    if data is None:
        return Baseline()
    try:
        raw = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Baseline(note="unreadable")
    if isinstance(raw, dict) and "version" in raw and raw["version"] != 1:
        return Baseline(note="unknown_version")
    try:
        return Baseline(file=ReviewFile.model_validate(raw))
    except ValidationError:
        return Baseline(note="unreadable")


def next_file(
    project: str,
    run_id: str,
    date: datetime,
    found: Sequence[FindingKey],
    analysis: CheckedAnalysis,
    wiring: Sequence[WiringFinding],
    warnings: Sequence[PinWarning],
    calculations: Sequence[Calculation],
    waivers: list[dict[str, object]],
) -> ReviewFile:
    """The review file for this run: every keyed finding (``keys.build``)
    with its title, and the baseline's waivers carried over."""
    titles = {("calculation", c.id): c.text for c in calculations}
    titles |= {("wiring", w.id): w.text for w in wiring}
    titles |= {("pin_warning", w.id): w.text for w in warnings}
    titles |= {("issue", h.id): h.title for h in analysis.hypotheses}
    severity = {h.id: h.severity for h in analysis.hypotheses}
    return ReviewFile(
        project=project,
        run_id=run_id,
        date=date,
        findings=[
            ReviewFinding(
                key=f.key,
                source=f.source,
                parts=f.parts,
                title=titles.get((f.source, f.id), ""),
                severity=severity.get(f.id) if f.source == "issue" else None,
            )
            for f in found
        ],
        waivers=waivers,
    )


def diff(baseline: Baseline, found: Sequence[FindingKey], next: ReviewFile) -> ReviewDiff:
    """This run's model issues (``found``, in rank order) against the
    baseline, and ``next`` to save. No baseline: no marks, nothing fixed."""
    if baseline.file is None:
        return ReviewDiff(note=baseline.note, next=next)
    earlier = [f for f in baseline.file.findings if f.source == "issue" and f.parts]
    issues = [f for f in found if f.source == "issue" and f.parts]
    # Best overlap first; ties go to the higher-ranked issue.
    pairs = sorted(
        (-jaccard(now.parts, old.parts), i, j)
        for i, now in enumerate(issues)
        for j, old in enumerate(earlier)
    )
    matched: dict[int, int] = {}
    taken: set[int] = set()
    for score, i, j in pairs:
        if -score < MATCH_JACCARD:
            break
        if i in matched or j in taken:
            continue
        matched[i] = j
        taken.add(j)
    marks: list[IssueMark] = []
    for i, now in enumerate(issues):
        if i not in matched:
            marks.append(IssueMark(id=now.id, mark="new"))
            continue
        old = earlier[matched[i]]
        changed = sorted(
            r for r in now.parts.keys() & old.parts.keys() if now.parts[r] != old.parts[r]
        )
        marks.append(
            IssueMark(id=now.id, mark="unchanged", earlier=old.title, changed_parts=changed)
        )
    return ReviewDiff(
        baseline_run_id=baseline.file.run_id,
        baseline_date=baseline.file.date,
        marks=marks,
        fixed=[old for j, old in enumerate(earlier) if j not in taken],
        next=next,
    )


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """|A ∩ B| / |A ∪ B| of two sets of part refs; 0 when both are empty."""
    x, y = set(a), set(b)
    union = x | y
    return len(x & y) / len(union) if union else 0.0
