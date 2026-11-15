"""The review file, the diff against it and its waivers (plan 011 steps 2–4).

``<project>.review.json`` sits next to the ``.kicad_pro`` and holds the last
review's findings (``ReviewFile``). Dropped with the project, it is the
baseline: each model issue of the new review is marked new or unchanged
against it, and the baseline's issues with no match are listed as fixed.
After the review the backend sends the next review file, which the user
saves over the old one, moving the baseline on.

Only model issues are compared. Their key is the set of pins their verified
cites name (``keys``), which two runs word and split differently, so two
issues match when those sets mostly overlap (Jaccard ≥ ``MATCH_JACCARD``),
each at most once, best overlap first; the UI calls a match "likely the
same". Pins, not whole parts, so two issues on different pins of the same
two ICs don't match. An issue whose cites name no pin can't be matched: a
new one gets no mark, an old one isn't listed as fixed.

A waiver (step 4) finds a model issue by the same loose match, and a backend
finding by its exact key. It lapses when a pin both it and the finding name
has a different fingerprint; a pin that drops out doesn't lapse it, since
runs drift on the pins they cite. Waivers are carried into the next file as
sent: only the user adds, replaces or removes one, in the browser.

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
    Waiver,
    WaiverHit,
    WiringFinding,
)

# Two model issues are likely the same when their cited pins overlap this
# much (|A ∩ B| / |A ∪ B|); tuned over parts on the recorded runs (plan 011
# step 1), kept for pins (step 3).
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
    if isinstance(raw, dict) and "version" in raw and raw["version"] != 2:
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
    waivers: list[Waiver],
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
                pins=f.pins,
                title=titles.get((f.source, f.id), ""),
                severity=severity.get(f.id) if f.source == "issue" else None,
            )
            for f in found
        ],
        waivers=waivers,
    )


def diff(baseline: Baseline, found: Sequence[FindingKey], next: ReviewFile) -> ReviewDiff:
    """This run's model issues (``found``, in rank order) against the
    baseline, the waivers of ``next`` that find a finding, and ``next`` to
    save. No baseline: no marks, nothing fixed, nothing waived."""
    if baseline.file is None:
        return ReviewDiff(note=baseline.note, found=list(found), next=next)
    earlier = [f for f in baseline.file.findings if f.source == "issue" and f.pins]
    issues = [f for f in found if f.source == "issue" and f.pins]
    matched = match([f.pins for f in issues], [f.pins for f in earlier])
    marks: list[IssueMark] = []
    for i, now in enumerate(issues):
        if i not in matched:
            marks.append(IssueMark(id=now.id, mark="new"))
            continue
        old = earlier[matched[i]]
        marks.append(
            IssueMark(
                id=now.id,
                mark="unchanged",
                earlier=old.title,
                changed_pins=changed_pins(now.pins, old.pins),
            )
        )
    taken = set(matched.values())
    return ReviewDiff(
        baseline_run_id=baseline.file.run_id,
        baseline_date=baseline.file.date,
        marks=marks,
        fixed=[old for j, old in enumerate(earlier) if j not in taken],
        waived=waive(next.waivers, found),
        found=list(found),
        next=next,
    )


def waive(waivers: Sequence[Waiver], found: Sequence[FindingKey]) -> list[WaiverHit]:
    """The waivers that find a finding of this run, in ``found``'s order.
    A backend finding is found by its exact key, a model issue by its cited
    pins (``match``); each finding takes at most one waiver, the first in the
    file for an exact key."""
    hits: dict[str, WaiverHit] = {}
    exact = {f.key: f for f in found if f.source != "issue"}
    for index, w in enumerate(waivers):
        f = exact.get(w.key) if w.source != "issue" else None
        if f is not None and f.id not in hits:
            hits[f.id] = _hit(f, index, w)
    issues = [f for f in found if f.source == "issue" and f.pins]
    loose = [(index, w) for index, w in enumerate(waivers) if w.source == "issue" and w.pins]
    for i, j in match([f.pins for f in issues], [w.pins for _, w in loose]).items():
        index, w = loose[j]
        hits[issues[i].id] = _hit(issues[i], index, w)
    return [hits[f.id] for f in found if f.id in hits]


def _hit(f: FindingKey, index: int, w: Waiver) -> WaiverHit:
    return WaiverHit(id=f.id, index=index, waiver=w, lapsed_pins=changed_pins(f.pins, w.pins))


def match(now: Sequence[dict[str, str]], old: Sequence[dict[str, str]]) -> dict[int, int]:
    """Which of ``old`` each of ``now`` is likely the same as, by index:
    pin sets overlapping by ``MATCH_JACCARD`` or more, each matched at most
    once, best overlap first; ties go to the earlier (higher-ranked) one."""
    pairs = sorted((-jaccard(a, b), i, j) for i, a in enumerate(now) for j, b in enumerate(old))
    matched: dict[int, int] = {}
    taken: set[int] = set()
    for score, i, j in pairs:
        if -score < MATCH_JACCARD:
            break
        if i in matched or j in taken:
            continue
        matched[i] = j
        taken.add(j)
    return matched


def changed_pins(now: dict[str, str], old: dict[str, str]) -> list[str]:
    """The pins both name whose fingerprint differs, sorted."""
    return sorted(p for p in now.keys() & old.keys() if now[p] != old[p])


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """|A ∩ B| / |A ∪ B| of two sets of pin refs; 0 when both are empty."""
    x, y = set(a), set(b)
    union = x | y
    return len(x & y) / len(union) if union else 0.0
