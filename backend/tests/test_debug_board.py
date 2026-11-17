"""The ``.kicad_pcb`` parser (``board.parse``): the same board saved by
KiCad 9 (nets by number) and KiCad 10 (nets by name) reads the same."""

import pathlib

import pytest

from app.features.debug.board import parse

GOLDEN = pathlib.Path(__file__).parent / "golden"


@pytest.mark.parametrize("version", [9, 10])
def test_the_golden_board_parses_the_same_from_kicad_9_and_10(version: int) -> None:
    board = parse((GOLDEN / f"ecc83_kicad{version}.kicad_pcb").read_text())
    assert board.copper_layers == ("F.Cu", "B.Cu")
    assert board.copper_mm("F.Cu") == pytest.approx(0.035)
    assert (len(board.pads), len(board.tracks), len(board.vias)) == (33, 59, 0)
    assert [(z.net, z.layers, z.filled) for z in board.zones] == [
        ("GND", frozenset({"B.Cu"}), True)
    ]
    assert [(f.net, f.layer) for f in board.fills] == [("GND", "B.Cu")]
    assert board.footprint_layers["C1"] == "F.Cu"
    assert {p.net for p in board.pads if p.ref == "C1"} <= {
        "GND",
        "Net-(P1-PM)",
        "Net-(U1A-G)",
        "Net-(U1A-K)",
        "Net-(U1B-K)",
        "Net-(P2-P1)",
        "Net-(P3-P1)",
        "Net-(P4-P1)",
        "Net-(P4-PM)",
    }


def test_kicad_9_and_10_give_identical_copper() -> None:
    old = parse((GOLDEN / "ecc83_kicad9.kicad_pcb").read_text())
    new = parse((GOLDEN / "ecc83_kicad10.kicad_pcb").read_text())
    assert sorted(old.pads, key=repr) == sorted(new.pads, key=repr)
    assert sorted(old.tracks, key=repr) == sorted(new.tracks, key=repr)
    assert old.fills == new.fills


def test_an_empty_board_has_no_copper() -> None:
    board = parse("(kicad_pcb)")
    assert board.pads == [] and board.tracks == [] and board.zones == []
