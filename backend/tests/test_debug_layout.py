"""Layout facts (``layout.check``) on small hand-built boards: decoupling
paths, ground fill under a switching regulator, track widths. Pure
functions, no model."""

import pytest

from app.features.debug import layout
from app.features.debug.board import Board, Fill, Pad, Track, Zone
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import DrcEntry, PowerRail, PowerTree, RailBudget

F = frozenset({"F.Cu"})


def _pad(ref: str, number: str, net: str, x: float, y: float) -> Pad:
    return Pad(ref, number, net, x, y, 0.5, F)


def _track(a: tuple[float, float], b: tuple[float, float], net: str = "VIN") -> Track:
    return Track(a, b, 0.25, "F.Cu", net)


def _square(net: str, layer: str, x0: float, y0: float, x1: float, y1: float) -> Fill:
    return Fill(net, layer, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def _rail(net: str, budget: RailBudget | None = None) -> PowerRail:
    return PowerRail(
        net=net,
        min=5.0,
        max=5.0,
        basis="named",
        inferred=False,
        note="",
        fed_by=["J1"],
        loads=["U1"],
        budget=budget,
    )


# --- decoupling ----------------------------------------------------------------------


def _ldo() -> Design:
    """U1's VIN on the VIN rail, C1 from VIN to GND."""
    parts = {"U1": Part("U1", "LDO", "", ""), "C1": Part("C1", "10u", "Device:C", "")}
    nets = {
        "VIN": [Node("U1", "1", "VIN"), Node("C1", "1", "")],
        "GND": [Node("U1", "2", "GND"), Node("C1", "2", "")],
    }
    return Design(parts, nets)


def _ldo_board(**copper: list) -> Board:
    """U1.1 at the origin, C1 at (10, 3)."""
    pads = [
        _pad("U1", "1", "VIN", 0, 0),
        _pad("U1", "2", "GND", 0, 2),
        _pad("C1", "1", "VIN", 10, 3),
        _pad("C1", "2", "GND", 11, 3),
    ]
    return Board(("F.Cu", "B.Cu"), {}, {"U1": "F.Cu", "C1": "F.Cu"}, pads, **copper)


TREE = PowerTree(rails=[_rail("VIN")], parts=[])


def test_decoupling_path_follows_tracks_through_a_t_junction() -> None:
    # U1.1 → (5, 0) ends on the middle of a vertical track, whose top end
    # runs to C1.1: 5 + 3 + 5 mm, not the 10.4 mm straight line.
    tracks = [
        _track((0, 0), (5, 0)),
        _track((5, -3), (5, 3)),
        _track((5, 3), (10, 3)),
    ]
    [fact] = layout.check(_ldo(), _ldo_board(tracks=tracks), TREE)
    assert fact.id == "pl1"
    assert fact.kind == "decoupling"
    assert fact.pins == ["U1.1", "C1.1"]
    assert fact.value == pytest.approx(13.0)
    assert fact.flagged
    assert "nearest decoupling capacitor C1, 13.0 mm" in fact.text
    assert "May fail, not verified." in fact.text


def test_a_short_decoupling_path_is_not_flagged() -> None:
    board = Board(
        ("F.Cu", "B.Cu"),
        {},
        {},
        [_pad("U1", "1", "VIN", 0, 0), _pad("C1", "1", "VIN", 3, 0), _pad("C1", "2", "GND", 4, 0)],
        tracks=[_track((0, 0), (3, 0))],
    )
    [fact] = layout.check(_ldo(), board, TREE, first_id=4)
    assert fact.id == "pl4"
    assert fact.value == pytest.approx(3.0)
    assert not fact.flagged
    assert "May fail" not in fact.text


def test_a_filled_zone_joins_the_pads_in_a_straight_line() -> None:
    fills = [_square("VIN", "F.Cu", -2, -2, 12, 5)]
    board = _ldo_board(zones=[Zone("VIN", F, True)], fills=fills)
    [fact] = layout.check(_ldo(), board, TREE)
    assert fact.value == pytest.approx((10**2 + 3**2) ** 0.5, abs=0.05)
    assert "across the VIN zone, counted in a straight line" in fact.text


def test_a_fill_with_a_thermal_relief_gap_still_joins_the_pad() -> None:
    # The fill stops 0.4 mm short of each pad's edge, as a relief does.
    fills = [
        Fill("VIN", "F.Cu", ((0.9, -1), (9.1, -1), (9.1, 4), (0.9, 4))),
    ]
    board = _ldo_board(zones=[Zone("VIN", F, True)], fills=fills)
    [fact] = layout.check(_ldo(), board, TREE)
    assert fact.value is not None


def test_no_copper_to_the_capacitor_is_flagged() -> None:
    [fact] = layout.check(_ldo(), _ldo_board(), TREE)
    assert fact.value is None
    assert fact.flagged
    assert "no copper joins it to a decoupling capacitor (C1)" in fact.text


def test_an_unfilled_zone_leaves_the_path_unknown_not_flagged() -> None:
    board = _ldo_board(zones=[Zone("VIN", F, False)])
    [fact] = layout.check(_ldo(), board, TREE)
    assert not fact.flagged
    assert "saved unfilled" in fact.text


def test_a_rail_without_a_decoupling_capacitor_gives_no_fact() -> None:
    design = _ldo()
    design = Design(
        design.parts,
        {"VIN": [Node("U1", "1", "VIN")], "GND": [Node("U1", "2", "GND")]},
    )
    assert layout.check(design, _ldo_board(), TREE) == []


# --- ground under a switching regulator -------------------------------------------------


def _buck() -> Design:
    """U2's SW pin on /SW with inductor L1."""
    parts = {"U2": Part("U2", "BUCK", "", ""), "L1": Part("L1", "10u", "Device:L", "")}
    nets = {
        "/SW": [Node("U2", "1", "SW"), Node("L1", "1", "")],
        "+5V": [Node("L1", "2", "")],
        "GND": [Node("U2", "2", "GND")],
    }
    return Design(parts, nets)


def _buck_board(**copper: list) -> Board:
    """U2's pads from (0, 0) to (2, 0), L1's from (5, 0) to (8, 0)."""
    pads = [
        _pad("U2", "1", "/SW", 0, 0),
        _pad("U2", "2", "GND", 2, 0),
        _pad("L1", "1", "/SW", 5, 0),
        _pad("L1", "2", "+5V", 8, 0),
    ]
    return Board(("F.Cu", "B.Cu"), {}, {"U2": "F.Cu", "L1": "F.Cu"}, pads, **copper)


NO_RAILS = PowerTree(rails=[], parts=[])
B = frozenset({"B.Cu"})


def test_ground_fill_under_the_regulator_and_its_inductor() -> None:
    board = _buck_board(zones=[Zone("GND", B, True)], fills=[_square("GND", "B.Cu", -5, -5, 15, 5)])
    [fact] = layout.check(_buck(), board, NO_RAILS)
    assert fact.kind == "ground_zone"
    assert fact.refs == ["U2", "L1"]
    assert fact.pins == ["U2.1"]
    assert fact.value == 100
    assert not fact.flagged
    assert "U2 (switching regulator, U2.1) and L1: 100% of the area under U2 and L1" in fact.text


def test_ground_fill_that_misses_the_inductor_is_flagged() -> None:
    board = _buck_board(zones=[Zone("GND", B, True)], fills=[_square("GND", "B.Cu", -5, -5, 3, 5)])
    [fact] = layout.check(_buck(), board, NO_RAILS)
    assert fact.value is not None and fact.value < layout.GROUND_FILL_PCT
    assert fact.flagged


def test_no_ground_zone_on_the_next_layer_is_flagged() -> None:
    [fact] = layout.check(_buck(), _buck_board(), NO_RAILS)
    assert fact.value == 0
    assert fact.flagged
    assert "no ground zone on B.Cu" in fact.text


def test_an_unfilled_ground_zone_is_unknown_not_flagged() -> None:
    [fact] = layout.check(_buck(), _buck_board(zones=[Zone("GND", B, False)]), NO_RAILS)
    assert fact.value is None
    assert not fact.flagged


# --- track width ----------------------------------------------------------------------


def test_ipc2221_width_for_one_amp_on_one_ounce_copper() -> None:
    # 1 A, 35 µm, 10 °C rise: about 12 mil outside, 2.6 times that inside.
    assert layout.ipc2221_width_mm(1.0, 0.035, external=True) == pytest.approx(0.30, abs=0.01)
    assert layout.ipc2221_width_mm(1.0, 0.035, external=False) == pytest.approx(0.78, abs=0.02)


def _budget(ma: float) -> RailBudget:
    return RailBudget(load_ma=ma, unknown=[], loads=[], rated_ma=None, rated_note="", over=False)


def test_a_track_narrower_than_ipc2221_for_the_rail_load_is_flagged() -> None:
    tree = PowerTree(rails=[_rail("VIN", _budget(1000))], parts=[])
    tracks = [_track((0, 0), (5, 0)), Track((5, 0), (10, 3), 0.2, "F.Cu", "VIN")]
    facts = layout.check(_ldo(), _ldo_board(tracks=tracks), tree)
    # The decoupling fact first, then the track width.
    assert [f.kind for f in facts] == ["decoupling", "track_width"]
    width = facts[1]
    assert width.id == "pl2"
    assert width.value == 0.2
    assert width.limit == pytest.approx(0.30, abs=0.01)
    assert width.flagged
    assert width.refs == ["J1"]


def test_an_unbudgeted_tree_gives_no_track_width() -> None:
    tracks = [Track((0, 0), (10, 3), 0.2, "F.Cu", "VIN")]
    facts = layout.check(_ldo(), _ldo_board(tracks=tracks), TREE)
    assert [f.kind for f in facts] == ["decoupling"]


# --- the board as the model reads it ---------------------------------------------------


def test_format_board_lists_drc_then_layout_facts() -> None:
    drc = [DrcEntry(id="drc-1", rule="clearance", severity="error", message="Too close", items=[])]
    facts = layout.check(_ldo(), _ldo_board(), TREE)
    text = layout.format_board(drc, facts)
    assert text.splitlines() == [
        "KiCad DRC:",
        "drc-1 [clearance] (error): Too close",
        "Layout facts:",
        f"pl1 FLAGGED: {facts[0].text}",
    ]


def test_format_board_says_why_drc_did_not_run() -> None:
    text = layout.format_board([], [], skipped="The board was saved by KiCad 10.0")
    assert "(not run: The board was saved by KiCad 10.0)" in text
    assert "(no DRC findings)" not in text
    assert "(none: " in text
