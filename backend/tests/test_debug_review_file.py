"""The review file, the diff against it and its waivers (``review_file``,
plan 011 steps 2–4).
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
    Waiver,
)

DATE = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)


def _issue(id: str, pins: dict[str, str]) -> FindingKey:
    return FindingKey(source="issue", id=id, key="issue:" + ",".join(sorted(pins)), pins=pins)


def _old(title: str, pins: dict[str, str], source: str = "issue") -> ReviewFinding:
    return ReviewFinding.model_validate(
        {
            "key": "issue:" + ",".join(sorted(pins)),
            "source": source,
            "pins": pins,
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
    sent = _baseline(_old("L2 on the rail", {"U2.9": "a"})).file
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
    assert review_file.read(_project(b'{"version": 2}')).note == "unreadable"
    assert review_file.read(_project(b"[]")).note == "unreadable"
    # Version 1 keyed whole parts (plan 011 step 2); its keys don't compare.
    for version in (1, 3):
        assert review_file.read(_project(json.dumps({"version": version}).encode())).note == (
            "unknown_version"
        )
    assert review_file.read(_project(b"{not json")).file is None


# --- the diff ---------------------------------------------------------------------------


def test_without_a_baseline_nothing_is_marked_or_fixed():
    got = review_file.diff(
        review_file.Baseline(note="unreadable"), [_issue("h1", {"U1.1": "a"})], NEXT
    )
    assert (got.marks, got.fixed, got.note, got.next) == ([], [], "unreadable", NEXT)
    # Still sent, so the first review's issues can be accepted.
    assert [f.id for f in got.found] == ["h1"]


def test_one_fixed_one_new_one_unchanged():
    baseline = _baseline(
        _old("L2 on the rail", {"U2.9": "a", "L1.1": "b"}),
        _old("BOOT0 floats", {"U1.60": "c", "R5.1": "d"}),
    )
    found = [_issue("h1", {"U2.9": "a", "L1.1": "b", "C3.1": "e"}), _issue("h2", {"J1.1": "f"})]
    got = review_file.diff(baseline, found, NEXT)
    assert [(m.id, m.mark, m.earlier) for m in got.marks] == [
        ("h1", "unchanged", "L2 on the rail"),
        ("h2", "new", None),
    ]
    assert [f.title for f in got.fixed] == ["BOOT0 floats"]
    assert (got.baseline_run_id, got.baseline_date) == ("r1", DATE)


def test_a_match_with_a_changed_pin_is_unchanged_and_names_the_pin():
    baseline = _baseline(_old("C12 is underrated", {"C12.1": "old", "U3.4": "u"}))
    got = review_file.diff(baseline, [_issue("h1", {"C12.1": "new", "U3.4": "u"})], NEXT)
    assert [(m.mark, m.changed_pins) for m in got.marks] == [("unchanged", ["C12.1"])]


def test_issues_on_other_pins_of_the_same_parts_dont_match():
    # Step 2 matched these over parts ({U16, U22}, 1.0); over pins they share none.
    baseline = _baseline(_old("MCU drives unpowered CP2102", {"U16.30": "a", "U22.4": "b"}))
    got = review_file.diff(baseline, [_issue("h1", {"U16.42": "c", "U22.25": "d"})], NEXT)
    assert [m.mark for m in got.marks] == ["new"]
    assert [f.title for f in got.fixed] == ["MCU drives unpowered CP2102"]


def test_overlap_below_a_half_is_no_match():
    baseline = _baseline(_old("old", {"U1.1": "a", "R1.1": "b", "R2.1": "c"}))
    got = review_file.diff(baseline, [_issue("h1", {"U1.1": "a", "R3.1": "d"})], NEXT)
    # |{U1.1}| / |{U1.1, R1.1, R2.1, R3.1}| = 0.25
    assert [m.mark for m in got.marks] == ["new"]
    assert [f.title for f in got.fixed] == ["old"]


def test_each_earlier_issue_matches_once_best_overlap_first():
    baseline = _baseline(_old("old", {"U1.1": "a", "R1.1": "b"}))
    found = [
        _issue("h1", {"U1.1": "a", "R1.1": "b", "C1.1": "c"}),
        _issue("h2", {"U1.1": "a", "R1.1": "b"}),
    ]
    got = review_file.diff(baseline, found, NEXT)
    # h2 overlaps fully (1.0), h1 by 2/3: h2 takes the match although h1 ranks higher.
    assert [(m.id, m.mark) for m in got.marks] == [("h1", "new"), ("h2", "unchanged")]
    assert got.fixed == []


def test_issues_that_name_no_pin_are_never_matched_marked_or_fixed():
    baseline = _baseline(
        _old("vague", {}), _old("C12 rating", {"C12.1": "a"}, source="calculation")
    )
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
        FindingKey(source="pin_warning", id="pw1", key="pin_convention:Q1", pins={"Q1.1": "q"}),
        _issue("h1", {"U2.9": "a"}),
    ]
    warning = PinWarning(id="pw1", ref="Q1", pins=[], nets=[], text="Q1 pinout")
    waivers = [_waiver("pin_convention:Q1", {"Q1.1": "q"}, source="pin_warning")]
    got = review_file.next_file("board", "r2", DATE, found, analysis, [], [warning], [], waivers)
    assert [(f.key, f.title, f.severity) for f in got.findings] == [
        ("pin_convention:Q1", "Q1 pinout", None),
        ("issue:U2.9", "L2 on the rail", "will_fail"),
    ]
    assert (got.version, got.project, got.run_id, got.waivers) == (2, "board", "r2", waivers)


# --- waivers ----------------------------------------------------------------------------


def _waiver(key: str, pins: dict[str, str], source: str = "issue") -> Waiver:
    return Waiver.model_validate(
        {
            "key": key,
            "source": source,
            "pins": pins,
            "title": "accepted",
            "reason": "never sees USB",
            "by": "tm",
            "date": DATE,
        }
    )


def _waived(waivers: list[Waiver], found: list[FindingKey]) -> list[tuple[str, int, list[str]]]:
    next = NEXT.model_copy(update={"waivers": waivers})
    got = review_file.diff(_baseline(), found, next)
    return [(h.id, h.index, h.lapsed_pins) for h in got.waived]


def test_a_waiver_quiets_the_issue_it_loosely_matches():
    waiver = _waiver("issue:C12.1,U3.4", {"C12.1": "a", "U3.4": "u"})
    found = [_issue("h1", {"J1.1": "j"}), _issue("h2", {"C12.1": "a", "U3.4": "u", "R1.1": "r"})]
    assert _waived([waiver], found) == [("h2", 0, [])]


def test_a_waiver_on_a_backend_finding_needs_its_exact_key():
    waiver = _waiver("wiring:U2.9", {"U2.9": "a"}, source="wiring")
    same = FindingKey(source="wiring", id="wf1", key="wiring:U2.9", pins={"U2.9": "a"})
    other = FindingKey(source="wiring", id="wf2", key="wiring:U2.10", pins={"U2.9": "a"})
    assert _waived([waiver], [other, same]) == [("wf1", 0, [])]
    # A model issue citing the same pin isn't the backend finding.
    assert _waived([waiver], [_issue("h1", {"U2.9": "a"})]) == []


def test_a_waiver_lapses_when_a_pin_both_name_changes():
    waiver = _waiver("issue:C12.1,U3.4", {"C12.1": "a", "U3.4": "u"})
    assert _waived([waiver], [_issue("h1", {"C12.1": "b", "U3.4": "u"})]) == [("h1", 0, ["C12.1"])]


def test_a_pin_that_drops_out_doesnt_lapse_a_waiver():
    waiver = _waiver("issue:C12.1,U3.4", {"C12.1": "a", "U3.4": "u"})
    # U3.4 isn't cited this run; C12.1 is unchanged. Jaccard 0.5.
    assert _waived([waiver], [_issue("h1", {"C12.1": "a"})]) == [("h1", 0, [])]


def test_each_waiver_quiets_one_finding_and_each_finding_takes_one_waiver():
    a = _waiver("issue:U1.1,R1.1", {"U1.1": "a", "R1.1": "b"})
    b = _waiver("wiring:U2.9", {"U2.9": "c"}, source="wiring")
    twin = _waiver("wiring:U2.9", {"U2.9": "old"}, source="wiring")
    found = [
        _issue("h1", {"U1.1": "a", "R1.1": "b"}),
        _issue("h2", {"U1.1": "a", "R1.1": "b"}),
        FindingKey(source="wiring", id="wf1", key="wiring:U2.9", pins={"U2.9": "c"}),
    ]
    # h2 ties h1 and loses to the higher rank; the first waiver for wf1's key wins.
    assert _waived([a, b, twin], found) == [("h1", 0, []), ("wf1", 1, [])]


def test_a_waiver_that_finds_nothing_is_kept_and_not_listed():
    waiver = _waiver("issue:U9.1", {"U9.1": "a"})
    next = NEXT.model_copy(update={"waivers": [waiver]})
    got = review_file.diff(_baseline(), [_issue("h1", {"J1.1": "j"})], next)
    assert (got.waived, got.next.waivers) == ([], [waiver])


def test_waivers_round_trip_through_the_review_file():
    sent = ReviewFile(
        project="board",
        run_id="r1",
        date=DATE,
        findings=[],
        waivers=[_waiver("issue:U1.1", {"U1.1": "a"})],
    )
    got = review_file.read(_project(sent.model_dump_json().encode()))
    assert got.file == sent
