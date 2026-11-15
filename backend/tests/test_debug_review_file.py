"""The review file and the diff against it (``review_file``, plan 011 step 2).
Hand-built findings; pure functions."""

import json
from datetime import UTC, datetime

from app.features.debug import review_file
from app.features.debug.project import Project
from app.features.debug.schemas import (
    CheckedAnalysis,
    CheckedClaim,
    FindingKey,
    Fix,
    Hypothesis,
    PinWarning,
    ReviewFile,
    ReviewFinding,
)

DATE = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def _issue(id: str, parts: dict[str, str]) -> FindingKey:
    return FindingKey(source="issue", id=id, key="issue:" + ",".join(sorted(parts)), parts=parts)


def _old(title: str, parts: dict[str, str], source: str = "issue") -> ReviewFinding:
    return ReviewFinding.model_validate(
        {
            "key": "issue:" + ",".join(sorted(parts)),
            "source": source,
            "parts": parts,
            "title": title,
        }
    )


def _baseline(*findings: ReviewFinding) -> review_file.Baseline:
    return review_file.Baseline(
        file=ReviewFile(project="board", run_id="r1", date=DATE, findings=list(findings))
    )


NEXT = ReviewFile(project="board", run_id="r2", date=DATE, findings=[])


def _project(review: bytes | None, folder: str = "") -> Project:
    files = {f"{folder}board.kicad_sch": b"(kicad_sch)"}
    if review is not None:
        files[f"{folder}board.review.json"] = review
    return Project(files=files, root=f"{folder}board.kicad_sch")


# --- reading the uploaded file --------------------------------------------------------


def test_no_review_file_is_no_baseline_and_no_note():
    assert review_file.read(_project(None)) == review_file.Baseline()


def test_a_review_file_next_to_the_root_sheet_is_the_baseline():
    sent = _baseline(_old("L2 on the rail", {"U2": "a"})).file
    assert sent is not None
    got = review_file.read(_project(sent.model_dump_json().encode(), folder="hw/"))
    assert got.file == sent and got.note is None


def test_another_projects_review_file_is_ignored():
    project = Project(
        files={"board.kicad_sch": b"(kicad_sch)", "old.review.json": b"{}"},
        root="board.kicad_sch",
    )
    assert review_file.read(project) == review_file.Baseline()


def test_malformed_or_unknown_version_files_give_no_baseline_and_say_why():
    assert review_file.read(_project(b"{not json")).note == "unreadable"
    assert review_file.read(_project(b'{"version": 1}')).note == "unreadable"
    assert review_file.read(_project(b"[]")).note == "unreadable"
    assert review_file.read(_project(json.dumps({"version": 2}).encode())).note == (
        "unknown_version"
    )
    assert review_file.read(_project(b"{not json")).file is None


# --- the diff ---------------------------------------------------------------------------


def test_without_a_baseline_nothing_is_marked_or_fixed():
    got = review_file.diff(
        review_file.Baseline(note="unreadable"), [_issue("h1", {"U1": "a"})], NEXT
    )
    assert (got.marks, got.fixed, got.note, got.next) == ([], [], "unreadable", NEXT)


def test_one_fixed_one_new_one_unchanged():
    baseline = _baseline(
        _old("L2 on the rail", {"U2": "a", "L1": "b"}),
        _old("BOOT0 floats", {"U1": "c", "R5": "d"}),
    )
    found = [_issue("h1", {"U2": "a", "L1": "b", "C3": "e"}), _issue("h2", {"J1": "f"})]
    got = review_file.diff(baseline, found, NEXT)
    assert [(m.id, m.mark, m.earlier) for m in got.marks] == [
        ("h1", "unchanged", "L2 on the rail"),
        ("h2", "new", None),
    ]
    assert [f.title for f in got.fixed] == ["BOOT0 floats"]
    assert (got.baseline_run_id, got.baseline_date) == ("r1", DATE)


def test_a_match_with_a_changed_part_is_unchanged_and_names_the_part():
    baseline = _baseline(_old("C12 is underrated", {"C12": "old", "U3": "u"}))
    got = review_file.diff(baseline, [_issue("h1", {"C12": "new", "U3": "u"})], NEXT)
    assert [(m.mark, m.changed_parts) for m in got.marks] == [("unchanged", ["C12"])]


def test_overlap_below_a_half_is_no_match():
    baseline = _baseline(_old("old", {"U1": "a", "R1": "b", "R2": "c"}))
    got = review_file.diff(baseline, [_issue("h1", {"U1": "a", "R3": "d"})], NEXT)
    # |{U1}| / |{U1, R1, R2, R3}| = 0.25
    assert [m.mark for m in got.marks] == ["new"]
    assert [f.title for f in got.fixed] == ["old"]


def test_each_earlier_issue_matches_once_best_overlap_first():
    baseline = _baseline(_old("old", {"U1": "a", "R1": "b"}))
    found = [_issue("h1", {"U1": "a", "R1": "b", "C1": "c"}), _issue("h2", {"U1": "a", "R1": "b"})]
    got = review_file.diff(baseline, found, NEXT)
    # h2 overlaps fully (1.0), h1 by 2/3: h2 takes the match although h1 ranks higher.
    assert [(m.id, m.mark) for m in got.marks] == [("h1", "new"), ("h2", "unchanged")]
    assert got.fixed == []


def test_issues_that_name_no_part_are_never_matched_marked_or_fixed():
    baseline = _baseline(_old("vague", {}), _old("C12 rating", {"C12": "a"}, source="calculation"))
    got = review_file.diff(baseline, [_issue("h1", {})], NEXT)
    # Neither empty set matches the other; a calculation is never compared.
    assert (got.marks, got.fixed) == ([], [])


# --- the next file ----------------------------------------------------------------------


def _hypothesis(id: str, title: str) -> Hypothesis[CheckedClaim]:
    return Hypothesis[CheckedClaim].model_validate(
        {
            "id": id,
            "rank": 1,
            "severity": "will_fail",
            "title": title,
            "summary": "s",
            "claims": [],
            "explains": [],
            "does_not_explain": [],
            "confirm": [],
            "fix": Fix(schematic=[], rework=[]),
        }
    )


def test_the_next_file_keeps_every_keyed_finding_with_its_title_and_the_waivers():
    analysis = CheckedAnalysis(
        hypotheses=[_hypothesis("h1", "L2 on the rail")], open_questions=[], glossary=[]
    )
    found = [
        FindingKey(source="pin_warning", id="pw1", key="pin_convention:Q1", parts={"Q1": "q"}),
        _issue("h1", {"U2": "a"}),
    ]
    warning = PinWarning(id="pw1", ref="Q1", pins=[], nets=[], text="Q1 pinout")
    waivers: list[dict[str, object]] = [{"key": "pin_convention:Q1", "reason": "checked"}]
    got = review_file.next_file("board", "r2", DATE, found, analysis, [], [warning], [], waivers)
    assert [(f.key, f.title, f.severity) for f in got.findings] == [
        ("pin_convention:Q1", "Q1 pinout", None),
        ("issue:U2", "L2 on the rail", "will_fail"),
    ]
    assert (got.version, got.project, got.run_id, got.waivers) == (1, "board", "r2", waivers)
