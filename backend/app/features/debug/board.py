"""The ``.kicad_pcb`` copper the layout facts are computed from.

``parse`` is pure: it reads the pads, tracks, vias, zones with their fills
and the stackup's copper thickness out of a board file. It doesn't need
kicad-cli, so a board kicad-cli can't load (a newer KiCad's) still gets its
layout facts. KiCad 9 and older name a net by number (``(net 2)``, with a
``(net 2 "GND")`` table at the top); KiCad 10 names it by name
(``(net "GND")``); both are read. ``layout.py`` judges what this returns.
"""

import math
import re
from dataclasses import dataclass, field

from app.features.debug.design import _children, _field, _parse_sexpr

DEFAULT_COPPER_MM = 0.035  # 1 oz, when the stackup doesn't say
_INNER = re.compile(r"^In(\d+)\.Cu$")

Point = tuple[float, float]


@dataclass(frozen=True)
class Pad:
    """One footprint pad, at its absolute board position (mm)."""

    ref: str
    number: str
    net: str
    x: float
    y: float
    radius: float  # half the pad's larger side: how close a track end counts as on it
    layers: frozenset[str]  # copper layers only


@dataclass(frozen=True)
class Track:
    """A track segment (an arc counts as the straight line between its ends)."""

    start: Point
    end: Point
    width: float
    layer: str
    net: str

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)


@dataclass(frozen=True)
class Via:
    x: float
    y: float
    radius: float
    net: str
    layers: frozenset[str]  # every copper layer it spans


@dataclass(frozen=True)
class Fill:
    """One filled polygon of a zone, on one layer."""

    net: str
    layer: str
    points: tuple[Point, ...]


@dataclass(frozen=True)
class Zone:
    net: str
    layers: frozenset[str]
    filled: bool  # False: saved without a fill, so what it covers isn't known


@dataclass(frozen=True)
class Board:
    copper_layers: tuple[str, ...]  # top to bottom: F.Cu, In1.Cu…, B.Cu
    thickness: dict[str, float]  # copper layer -> mm
    footprint_layers: dict[str, str]  # ref -> F.Cu or B.Cu
    pads: list[Pad] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)

    def copper_mm(self, layer: str) -> float:
        """The layer's copper thickness, 35 µm when the stackup doesn't say."""
        return self.thickness.get(layer, DEFAULT_COPPER_MM)

    def is_outer(self, layer: str) -> bool:
        return layer in ("F.Cu", "B.Cu")

    def adjacent(self, layer: str) -> str | None:
        """The copper layer next to ``layer`` (towards the board's middle)."""
        layers = self.copper_layers
        if layer not in layers or len(layers) < 2:
            return None
        i = layers.index(layer)
        return layers[i + 1] if i + 1 < len(layers) else layers[i - 1]


def _layer_order(name: str) -> int:
    if name == "F.Cu":
        return 0
    if name == "B.Cu":
        return 1000
    m = _INNER.match(name)
    return int(m.group(1)) if m else 999


def _copper(names: list[str], all_layers: tuple[str, ...]) -> frozenset[str]:
    """The copper layers a ``(layers …)`` list names; ``*.Cu`` and ``F&B.Cu``
    expand."""
    got: set[str] = set()
    for n in names:
        if n == "*.Cu":
            got.update(all_layers)
        elif n == "F&B.Cu":
            got.update(("F.Cu", "B.Cu"))
        elif n.endswith(".Cu"):
            got.add(n)
    return frozenset(got)


def _xy(node: list) -> Point:
    return float(node[1]), float(node[2])


def _num(node: list, name: str, default: float = 0.0) -> float:
    value = _field(node, name)
    try:
        return float(value)
    except ValueError:
        return default


def _args(node: list, name: str) -> list[str]:
    """The plain arguments of ``node``'s first ``(name …)`` child."""
    found = _children(node, name)
    return [a for a in found[0][1:] if isinstance(a, str)] if found else []


def parse(text: str) -> Board:
    """Parse a ``.kicad_pcb``. Raises ``ValueError`` on anything that isn't
    one."""
    root = _parse_sexpr(text)
    if not root or root[0] != "kicad_pcb":
        raise ValueError("not a KiCad board: missing (kicad_pcb ...)")

    numbered = {n[1]: n[2] for n in _children(root, "net") if len(n) > 2}

    def net_of(node: list) -> str:
        """The net a pad, track, via or zone is on: a number (KiCad ≤9) or a
        name (KiCad 10)."""
        name = _field(node, "net_name")
        if name:
            return name
        found = _children(node, "net")
        if not found or len(found[0]) < 2:
            return ""
        if len(found[0]) > 2:
            return str(found[0][2])
        return numbered.get(found[0][1], found[0][1])

    copper: list[str] = []
    for table in _children(root, "layers"):
        for entry in table[1:]:
            if isinstance(entry, list) and len(entry) > 1 and str(entry[1]).endswith(".Cu"):
                copper.append(entry[1])
    copper_layers = tuple(sorted(set(copper), key=_layer_order))

    thickness: dict[str, float] = {}
    for setup in _children(root, "setup"):
        for stackup in _children(setup, "stackup"):
            for layer in _children(stackup, "layer"):
                if len(layer) > 1 and str(layer[1]).endswith(".Cu"):
                    mm = _num(layer, "thickness")
                    if mm > 0:
                        thickness[layer[1]] = mm

    board = Board(copper_layers=copper_layers, thickness=thickness, footprint_layers={})

    for fp in _children(root, "footprint"):
        ref = next(
            (p[2] for p in _children(fp, "property") if len(p) > 2 and p[1] == "Reference"),
            "",
        ) or next(
            (t[2] for t in _children(fp, "fp_text") if len(t) > 2 and t[1] == "reference"),
            "",
        )
        at = _children(fp, "at")
        if not ref or not at:
            continue
        fx, fy = _xy(at[0])
        theta = math.radians(float(at[0][3])) if len(at[0]) > 3 else 0.0
        board.footprint_layers[ref] = _field(fp, "layer") or "F.Cu"
        for pad in _children(fp, "pad"):
            pat = _children(pad, "at")
            size = _children(pad, "size")
            if len(pad) < 2 or not pat:
                continue
            px, py = _xy(pat[0])
            # KiCad's y axis points down and a footprint's angle turns it
            # counter-clockwise on screen, so the pad offset turns by -theta.
            x = fx + px * math.cos(theta) + py * math.sin(theta)
            y = fy - px * math.sin(theta) + py * math.cos(theta)
            radius = max(float(size[0][1]), float(size[0][2])) / 2 if size else 0.5
            board.pads.append(
                Pad(
                    ref=ref,
                    number=str(pad[1]),
                    net=net_of(pad),
                    x=round(x, 4),
                    y=round(y, 4),
                    radius=radius,
                    layers=_copper(_args(pad, "layers"), copper_layers),
                )
            )

    for kind in ("segment", "arc"):
        for seg in _children(root, kind):
            start, end = _children(seg, "start"), _children(seg, "end")
            if not start or not end:
                continue
            board.tracks.append(
                Track(
                    start=_xy(start[0]),
                    end=_xy(end[0]),
                    width=_num(seg, "width", 0.25),
                    layer=_field(seg, "layer"),
                    net=net_of(seg),
                )
            )

    for via in _children(root, "via"):
        at = _children(via, "at")
        if not at:
            continue
        # A via is treated as joining every copper layer between its two
        # ends; blind and buried vias name those ends the same way.
        ends = sorted(_copper(_args(via, "layers"), copper_layers), key=_layer_order)
        spans = (
            frozenset(
                lay
                for lay in copper_layers
                if _layer_order(ends[0]) <= _layer_order(lay) <= _layer_order(ends[-1])
            )
            if ends
            else frozenset(copper_layers)
        )
        board.vias.append(
            Via(
                x=float(at[0][1]),
                y=float(at[0][2]),
                radius=_num(via, "size", 0.6) / 2,
                net=net_of(via),
                layers=spans,
            )
        )

    for zone in _children(root, "zone"):
        net = net_of(zone)
        layers = _copper(_args(zone, "layers") or _args(zone, "layer"), copper_layers)
        fills = _children(zone, "filled_polygon")
        board.zones.append(Zone(net=net, layers=layers, filled=bool(fills)))
        for poly in fills:
            pts = _children(poly, "pts")
            points = tuple(_xy(p) for p in _children(pts[0], "xy")) if pts else ()
            if points:
                board.fills.append(Fill(net=net, layer=_field(poly, "layer"), points=points))

    return board
