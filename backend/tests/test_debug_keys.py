"""Each finding's stable key and its pins' fingerprints (``keys.build``,
plan 011 steps 1 and 3). Hand-built designs; pure functions."""

from app.features.debug import keys
from app.features.debug.design import Design, Node, Part
from app.features.debug.schemas import (
    Calculation,
    CheckedAnalysis,
    CheckedClaim,
    Cite,
    Fix,
    Hypothesis,
    PinWarning,
    WiringFinding,
)


def _part(ref: str, value: str) -> Part:
    return Part(ref, value, "", f"fp:{ref[0]}")


def _design(values: dict[str, str] | None = None, reverse: bool = False) -> Design:
    """U1 (a regulator) with C1 on its output, R1/R2 a divider into its
    feedback pin (an auto-named net), and J1 and LED D1 far off on GND.
    ``values`` overrides parts' values; ``reverse`` lists parts and nets
    backwards."""
    vals = {"U1": "TPS62162", "C1": "10uF", "R1": "100k", "R2": "22k", "J1": "Conn", "D1": "LED"}
    vals |= values or {}
    parts = {ref: _part(ref, v) for ref, v in vals.items()}
    nets = {
        "+3V3": [Node("U1", "5", "VOS"), Node("C1", "1", ""), Node("R1", "1", "")],
        "Net-(U1-FB)": [Node("U1", "3", "FB"), Node("R1", "2", ""), Node("R2", "1", "")],
        "GND": [Node("U1", "2", "GND"), Node("C1", "2", ""), Node("R2", "2", "")]
        + [Node("J1", "2", ""), Node("D1", "2", "")],
        "Net-(D1-A)": [Node("J1", "1", ""), Node("D1", "1", "")],
    }
    if reverse:
        parts = dict(reversed(parts.items()))
        nets = {name: list(reversed(nodes)) for name, nodes in reversed(nets.items())}
    return Design(parts, nets)


def _calc(id: str, kind: str, ic: str, pins: list[str]) -> Calculation:
    return Calculation.model_validate(
        {
            "id": id,
            "kind": kind,
            "ic": ic,
            "pins": pins,
            "value": None,
            "unit": "",
            "expected": None,
            "flagged": True,
            "text": "",
        }
    )


CALCS = [
    _calc("cf1", "divider", "U1", ["U1.3", "R1.2", "R2.1"]),
    _calc("cf2", "part_rating", "C1", ["C1.1", "C1.2"]),
    _calc("cf3", "connector_esd", "J1", ["J1.1"]),
]
WIRING = [
    WiringFinding(
        id="wf1",
        pin="U1.5",
        pin_name="VOS",
        net="+3V3",
        conflicts=["C1.1"],
        inductors=[],
        nets=["+3V3"],
        text="",
    )
]
WARNINGS = [PinWarning(id="pw1", ref="D1", pins=["D1.1"], nets=[], text="")]


def _claim(*cites: Cite, downgraded: bool = False) -> CheckedClaim:
    return CheckedClaim(
        text="",
        evidence="none" if downgraded else "schematic",
        cites=list(cites),
        downgraded=downgraded,
    )


def _pin(pin: str) -> Cite:
    return Cite(kind="pin_on_net", pin=pin, net=None, id=None)


def _analysis(*claims: CheckedClaim) -> CheckedAnalysis:
    return CheckedAnalysis(
        hypotheses=[
            Hypothesis[CheckedClaim](
                id="h1",
                rank=1,
                severity="may_fail",
                title="",
                summary="",
                claims=list(claims),
                explains=[],
                does_not_explain=[],
                confirm=[],
                fix=Fix(schematic=[], rework=[]),
            )
        ],
        open_questions=[],
        glossary=[],
    )


ISSUE = _analysis(
    _claim(_pin("U1.3"), Cite(kind="reference", pin=None, net=None, id="cf2")),
    _claim(_pin("D1.1"), downgraded=True),
)


def _keys(design: Design) -> dict[str, tuple[str, dict[str, str]]]:
    found = keys.build(design, ISSUE, WIRING, WARNINGS, CALCS)
    return {f.id: (f.key, f.pins) for f in found}


def test_each_finding_is_keyed_by_what_it_is_about_and_names_its_pins():
    found = {
        f.id: (f.source, f.key, sorted(f.pins))
        for f in keys.build(_design(), ISSUE, WIRING, WARNINGS, CALCS)
    }
    assert found == {
        "cf1": ("calculation", "divider:U1.3", ["R1.2", "R2.1", "U1.3"]),
        # About the whole part: all its pins.
        "cf2": ("calculation", "part_rating:C1", ["C1.1", "C1.2"]),
        "cf3": ("calculation", "connector_esd:J1", ["J1.1", "J1.2"]),
        # The finding's pin alone, not the pins it conflicts with.
        "wf1": ("wiring", "wiring:U1.5", ["U1.5"]),
        "pw1": ("pin_warning", "pin_convention:D1", ["D1.1", "D1.2"]),
        # U1.3 from its pin cite, C1's pins from the calculation it cites;
        # not D1.1, whose claim was downgraded.
        "h1": ("issue", "issue:C1.1,C1.2,U1.3", ["C1.1", "C1.2", "U1.3"]),
    }


def test_a_cited_calculation_with_no_pins_adds_none():
    bare = [_calc("cf1", "part_rating", "C1", [])]
    cites = _analysis(_claim(_pin("U1.3"), Cite(kind="reference", pin=None, net=None, id="cf1")))
    found = {f.id: f for f in keys.build(_design(), cites, [], [], bare)}
    assert found["h1"].key == "issue:U1.3"
    assert sorted(found["cf1"].pins) == ["C1.1", "C1.2"]


def test_reordered_parts_and_nets_give_the_same_keys_and_fingerprints():
    assert _keys(_design(reverse=True)) == _keys(_design())


def test_a_renamed_net_gives_the_same_keys_and_fingerprints():
    renamed = _design()
    renamed.nets["Net-(U1-Pad3)"] = renamed.nets.pop("Net-(U1-FB)")
    assert _keys(Design(renamed.parts, renamed.nets)) == _keys(_design())


def test_a_changed_value_changes_the_fingerprint_not_the_key():
    before, after = _keys(_design()), _keys(_design({"C1": "22uF"}))
    assert after["cf2"][0] == before["cf2"][0] == "part_rating:C1"
    assert after["cf2"][1]["C1.1"] != before["cf2"][1]["C1.1"]


def test_a_changed_neighbour_changes_only_the_pin_on_its_net():
    # R2 shares U1's feedback net (U1.3), so that pin's circuit changed;
    # U1's output pin (U1.5) and C1 share only +3V3 and GND with it.
    before, after = _keys(_design()), _keys(_design({"R2": "47k"}))
    assert after["cf1"][1]["U1.3"] != before["cf1"][1]["U1.3"]
    assert after["wf1"] == before["wf1"]
    assert after["cf2"] == before["cf2"]


def test_a_far_part_on_ground_changes_no_fingerprint():
    # D1 is on GND with U1 and C1, but GND counts by name only.
    before, after = _keys(_design()), _keys(_design({"D1": "LED red"}))
    assert after["h1"] == before["h1"]
    assert after["pw1"][1]["D1.1"] != before["pw1"][1]["D1.1"]


def test_a_big_net_counts_by_name_only():
    small = _design()
    bus = {f"U{i}": _part(f"U{i}", "TMP117") for i in range(2, 10)}
    sda = [Node("U1", "4", "SDA"), *(Node(ref, "1", "SDA") for ref in bus)]
    big = Design(small.parts | bus, small.nets | {"SDA": sda})
    changed = Design(big.parts | {"U9": _part("U9", "TMP116")}, big.nets)
    # Nine pins: the bus is big, so U1.4's fingerprint ignores U9's value.
    assert keys.fingerprints(big)["U1.4"] == keys.fingerprints(changed)["U1.4"]
    eight = Design(big.parts, big.nets | {"SDA": sda[:-1]})
    changed8 = Design(eight.parts | {"U8": _part("U8", "TMP116")}, eight.nets)
    assert keys.fingerprints(eight)["U1.4"] != keys.fingerprints(changed8)["U1.4"]
