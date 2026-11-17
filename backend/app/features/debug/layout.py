"""Layout facts: what the board's copper says about the circuit, each judged
against a fixed limit (plan 013 step 2).

``check`` is pure. It takes the parsed board (``board.parse``), the
netlist and the power tree and returns ``LayoutFact``s, numbered pl1, pl2…:

- decoupling: each IC supply pin's (a supply or input pin on a rail of the
  power tree) copper path to the nearest capacitor between its net and
  ground, pad to pad along tracks and vias, and across a
  filled zone of that net in a straight line. Past 5 mm it may fail.
- ground_zone: a switching regulator (an IC with an SW or LX pin) and the
  inductors on its switch node, against the ground fill on the copper layer
  next to theirs. Less than 75% of the area under their pads filled may fail.
  A ground zone saved unfilled leaves it unknown, never flagged.
- track_width: on a rail whose budget has a known load, the narrowest track
  against IPC-2221's width for that load at 10 °C rise, with the stackup's
  copper thickness. Narrower may fail.

A fact past its limit reads "may fail, not verified": each limit is a rule
of thumb, not the part's datasheet. Every fact gives its number. Track
widths come last, so the facts before them keep their ids once the
datasheets' budgets add them.
"""

import heapq
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.features.debug import calc
from app.features.debug.board import Board, Fill, Pad, Point
from app.features.debug.design import Design, is_ic, is_inductor, short_net_name
from app.features.debug.power import is_supply_pin, is_switch_pin
from app.features.debug.schemas import DrcEntry, LayoutFact, PowerTree

DECOUPLING_MM = 5.0
GROUND_FILL_PCT = 75.0
RISE_C = 10.0
# IPC-2221: I = k · ΔT^0.44 · A^0.725, A in mil², I in A.
_K_EXTERNAL = 0.048
_K_INTERNAL = 0.024
_MIL = 0.0254  # mm
_GRID_MM = 0.1  # the step of the grid that measures fill under the pads
_RELIEF_MM = 0.6  # past KiCad's default thermal-relief gap (0.5 mm)
_MAY_FAIL = "May fail, not verified."


def check(design: Design, board: Board, tree: PowerTree, first_id: int = 1) -> list[LayoutFact]:
    """The layout facts for one board, numbered from ``pl{first_id}``.
    ``tree`` without budgets (the summary's) gives no track widths."""
    facts = _decoupling(design, board, tree) + _ground_zones(design, board)
    facts += _track_widths(board, tree)
    return [f.model_copy(update={"id": f"pl{first_id + i}"}) for i, f in enumerate(facts)]


def format_board(drc: Sequence[DrcEntry], facts: Sequence[LayoutFact], skipped: str = "") -> str:
    """The board as the model reads it: KiCad's DRC entries, then the layout
    facts, with ids it can cite."""
    lines = ["KiCad DRC:"]
    if skipped:
        lines.append(f"(not run: {skipped})")
    for e in drc:
        lines.append(f"{e.id} [{e.rule}] ({e.severity}): {e.message}")
        lines += [f"    {item}" for item in e.items]
    if not skipped and not drc:
        lines.append("(no DRC findings)")
    lines.append("Layout facts:")
    lines += [f"{f.id}{' FLAGGED' if f.flagged else ''}: {f.text}" for f in facts]
    if not facts:
        lines.append("(none: no IC supply pin, switching regulator or budgeted rail on the board)")
    return "\n".join(lines) + "\n"


# --- decoupling ----------------------------------------------------------------------


def _decoupling(design: Design, board: Board, tree: PowerTree) -> list[LayoutFact]:
    pins_of: dict[str, list[tuple[str, str]]] = {}
    for net, nodes in design.nets.items():
        for n in nodes:
            pins_of.setdefault(n.ref, []).append((n.pin, net))
    pads = {(p.ref, p.number): p for p in board.pads}
    facts = []
    for rail in tree.rails:
        nodes = design.nets.get(rail.net, [])
        caps = sorted(
            {
                n.ref
                for n in nodes
                if n.ref.rstrip("0123456789") == "C"
                and len(pins_of.get(n.ref, [])) == 2
                and any(calc.is_ground(net) for _, net in pins_of[n.ref])
            },
            key=_ref_key,
        )
        cap_pads = [pads[(n.ref, n.pin)] for n in nodes if n.ref in caps and (n.ref, n.pin) in pads]
        # Every IC supply pin on the rail, not only the tree's loads: a
        # regulator the tree doesn't recognise still needs its input
        # capacitor.
        supply = [
            n
            for n in nodes
            if is_ic(n.ref) and is_supply_pin(n.function) and (n.ref, n.pin) in pads
        ]
        seen: set[str] = set()
        for n in sorted(supply, key=lambda n: (_ref_key(n.ref), n.pin)):
            if n.pin_ref in seen or not cap_pads:
                continue
            seen.add(n.pin_ref)
            facts.append(
                _decoupling_fact(board, pads[(n.ref, n.pin)], n.function, rail.net, cap_pads)
            )
    return facts


def _decoupling_fact(
    board: Board, pin: Pad, function: str, net: str, caps: list[Pad]
) -> LayoutFact:
    pin_ref = f"{pin.ref}.{pin.number}"
    name = f"{pin_ref} ({function}, {short_net_name(net)})" if function else pin_ref
    found = _paths(board, pin, caps)
    if not found:
        unfilled = any(z.net == pin.net and not z.filled for z in board.zones)
        cap_refs = ", ".join(dict.fromkeys(c.ref for c in caps))
        if unfilled:
            text = (
                f"{name}: no track joins it to a decoupling capacitor ({cap_refs}), and the "
                f"{short_net_name(net)} zone was saved unfilled, so the copper path isn't known."
            )
            flagged = False
        else:
            text = f"{name}: no copper joins it to a decoupling capacitor ({cap_refs}). {_MAY_FAIL}"
            flagged = True
        return LayoutFact(
            id="",
            kind="decoupling",
            refs=[pin.ref, *dict.fromkeys(c.ref for c in caps)],
            pins=[pin_ref],
            net=net,
            value=None,
            limit=DECOUPLING_MM,
            unit="mm",
            flagged=flagged,
            text=text,
        )
    cap, mm, through_zone = min(found, key=lambda f: f[1])
    how = f"{mm:.1f} mm of copper from pad to pad"
    if through_zone:
        how += f", part of it across the {short_net_name(net)} zone, counted in a straight line"
    flagged = mm > DECOUPLING_MM
    text = f"{name}: nearest decoupling capacitor {cap.ref}, {how}."
    text += f" Over {DECOUPLING_MM:g} mm. {_MAY_FAIL}" if flagged else ""
    return LayoutFact(
        id="",
        kind="decoupling",
        refs=[pin.ref, cap.ref],
        pins=[pin_ref, f"{cap.ref}.{cap.number}"],
        net=net,
        value=round(mm, 1),
        limit=DECOUPLING_MM,
        unit="mm",
        flagged=flagged,
        text=text,
    )


@dataclass(frozen=True)
class _Node:
    point: Point
    layers: frozenset[str]
    reach: float  # how far from ``point`` copper still touches it


def _paths(board: Board, start: Pad, targets: list[Pad]) -> list[tuple[Pad, float, bool]]:
    """Each target pad the start pad's copper reaches: (pad, mm, whether the
    shortest path crosses a zone). Tracks count their length; a filled zone
    of the net joins what lies on it in a straight line."""
    net = start.net
    nodes: list[_Node] = []
    edges: dict[int, list[tuple[int, float, bool]]] = {}

    def add(node: _Node) -> int:
        nodes.append(node)
        edges[len(nodes) - 1] = []
        return len(nodes) - 1

    def join(a: int, b: int, mm: float, zone: bool = False) -> None:
        edges[a].append((b, mm, zone))
        edges[b].append((a, mm, zone))

    pads = [p for p in board.pads if p.net == net]
    pad_ids = {
        (p.ref, p.number, p.x, p.y): add(_Node((p.x, p.y), p.layers, p.radius)) for p in pads
    }
    vias = [add(_Node((v.x, v.y), v.layers, v.radius)) for v in board.vias if v.net == net]
    tracks = [t for t in board.tracks if t.net == net]
    ends: list[tuple[int, int]] = []
    for t in tracks:
        a = add(_Node(t.start, frozenset({t.layer}), t.width / 2))
        b = add(_Node(t.end, frozenset({t.layer}), t.width / 2))
        join(a, b, t.length)
        ends.append((a, b))

    points = [*pad_ids.values(), *vias]
    for i, t in enumerate(tracks):
        for end in ends[i]:
            p = nodes[end]
            for other in points:
                o = nodes[other]
                if t.layer in o.layers and math.dist(p.point, o.point) <= o.reach + 1e-6:
                    join(end, other, math.dist(p.point, o.point))
            for j, u in enumerate(tracks):
                if j == i or u.layer != t.layer:
                    continue
                d, along = _to_segment(p.point, u.start, u.end)
                if d <= (u.width + t.width) / 2 + 1e-6:
                    join(end, ends[j][0], along + d)
                    join(end, ends[j][1], max(u.length - along, 0.0) + d)
    # A pad and a via on top of each other (via in pad).
    for a in points:
        for b in points:
            if a < b and nodes[a].layers & nodes[b].layers:
                d = math.dist(nodes[a].point, nodes[b].point)
                if d <= max(nodes[a].reach, nodes[b].reach):
                    join(a, b, d)
    # A filled zone of the net joins whatever lies on it, in a straight line.
    fills = [f for f in board.fills if f.net == net]
    on_fill: list[int] = []
    for i, item in enumerate(nodes):
        if any(f.layer in item.layers and _touches(item, f) for f in fills):
            on_fill.append(i)
    for x, a in enumerate(on_fill):
        for b in on_fill[x + 1 :]:
            join(a, b, math.dist(nodes[a].point, nodes[b].point), zone=True)

    source = pad_ids[(start.ref, start.number, start.x, start.y)]
    dist, zoned = _dijkstra(edges, source)
    found = []
    for pad in targets:
        node = pad_ids.get((pad.ref, pad.number, pad.x, pad.y))
        if node is not None and node in dist:
            found.append((pad, dist[node], zoned[node]))
    return found


def _dijkstra(
    edges: dict[int, list[tuple[int, float, bool]]], source: int
) -> tuple[dict[int, float], dict[int, bool]]:
    dist = {source: 0.0}
    zoned = {source: False}
    queue = [(0.0, source)]
    while queue:
        d, node = heapq.heappop(queue)
        if d > dist[node]:
            continue
        for nxt, mm, zone in edges[node]:
            nd = d + mm
            if nd < dist.get(nxt, math.inf) - 1e-9:
                dist[nxt] = nd
                zoned[nxt] = zoned[node] or zone
                heapq.heappush(queue, (nd, nxt))
    return dist, zoned


def _to_segment(p: Point, a: Point, b: Point) -> tuple[float, float]:
    """The distance from ``p`` to segment ab, and how far along ab its
    nearest point is."""
    length = math.dist(a, b)
    if length == 0:
        return math.dist(p, a), 0.0
    t = ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / length**2
    t = min(max(t, 0.0), 1.0)
    near = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return math.dist(p, near), t * length


def _touches(node: _Node, fill: Fill) -> bool:
    """Whether a pad, via or track end lies on a fill. A fill keeps a
    thermal-relief gap around a pad of its own net, so a ring just past the
    gap counts too: clearance only parts different nets."""
    if _inside(node.point, fill):
        return True
    r = node.reach + _RELIEF_MM
    x, y = node.point
    return any(
        _inside((x + r * math.cos(a), y + r * math.sin(a)), fill)
        for a in (i * math.pi / 4 for i in range(8))
    )


def _inside(p: Point, fill: Fill) -> bool:
    """Even-odd point in polygon. KiCad saves a fill with its holes joined to
    the outline by slits, so even-odd gets the holes right."""
    x, y = p
    pts = fill.points
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


# --- ground under a switching regulator -----------------------------------------------


def _ground_zones(design: Design, board: Board) -> list[LayoutFact]:
    facts = []
    for ref in sorted({n.ref for nodes in design.nets.values() for n in nodes}, key=_ref_key):
        if not is_ic(ref) or ref not in board.footprint_layers:
            continue
        sw = [
            (n, net)
            for net, nodes in design.nets.items()
            for n in nodes
            if n.ref == ref and is_switch_pin(n.function)
        ]
        if not sw:
            continue
        inductors = sorted(
            {
                n.ref
                for _, net in sw
                for n in design.nets.get(net, [])
                if is_inductor(design, n.ref) and n.ref in board.footprint_layers
            },
            key=_ref_key,
        )
        layer = board.footprint_layers[ref]
        under = board.adjacent(layer)
        if under is None:
            continue
        facts.append(_ground_fact(board, ref, inductors, sw[0][0].pin_ref, layer, under))
    return facts


def _ground_fact(
    board: Board, ic: str, inductors: list[str], sw_pin: str, layer: str, under: str
) -> LayoutFact:
    refs = [ic, *inductors]
    parts = " and ".join(refs) if len(refs) < 3 else f"{', '.join(refs[:-1])} and {refs[-1]}"
    who = f"{ic} (switching regulator, {sw_pin})" + (
        f" and {' and '.join(inductors)}" if inductors else ""
    )
    zones = [z for z in board.zones if calc.is_ground(z.net) and under in z.layers]
    fills = [f for f in board.fills if calc.is_ground(f.net) and f.layer == under]

    def fact(value: float | None, flagged: bool, text: str) -> LayoutFact:
        return LayoutFact(
            id="",
            kind="ground_zone",
            refs=refs,
            pins=[sw_pin],
            net=zones[0].net if zones else "",
            value=value,
            limit=GROUND_FILL_PCT,
            unit="%",
            flagged=flagged,
            text=text,
        )

    if not zones:
        return fact(
            0.0,
            True,
            f"{who}: no ground zone on {under}, the copper layer next to {layer}. {_MAY_FAIL}",
        )
    if not fills:
        return fact(
            None,
            False,
            f"{who}: the ground zone on {under} was saved unfilled, so what lies under "
            f"{parts} isn't known.",
        )
    pct = _coverage(board, refs, fills)
    flagged = pct < GROUND_FILL_PCT
    text = (
        f"{who}: {pct:.0f}% of the area under {parts}'s pads has ground fill on {under}, "
        f"the copper layer next to {layer}."
    )
    text += f" Under {GROUND_FILL_PCT:g}%. {_MAY_FAIL}" if flagged else ""
    return fact(round(pct, 0), flagged, text)


def _coverage(board: Board, refs: Iterable[str], fills: list[Fill]) -> float:
    """The share (%) of grid points under each part's pads, within the box
    around them, that lie in a fill."""
    total = covered = 0
    for ref in refs:
        pads = [p for p in board.pads if p.ref == ref]
        if not pads:
            continue
        x0 = min(p.x - p.radius for p in pads)
        x1 = max(p.x + p.radius for p in pads)
        y0 = min(p.y - p.radius for p in pads)
        y1 = max(p.y + p.radius for p in pads)
        nx = max(int((x1 - x0) / _GRID_MM), 1)
        ny = max(int((y1 - y0) / _GRID_MM), 1)
        for i in range(nx + 1):
            for j in range(ny + 1):
                pt = (x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny)
                total += 1
                covered += any(_inside(pt, f) for f in fills)
    return 100.0 * covered / total if total else 0.0


# --- track width ----------------------------------------------------------------------


def ipc2221_width_mm(amps: float, copper_mm: float, external: bool) -> float:
    """IPC-2221's track width (mm) for ``amps`` at 10 °C rise."""
    k = _K_EXTERNAL if external else _K_INTERNAL
    area_mil2 = (amps / (k * RISE_C**0.44)) ** (1 / 0.725)
    return area_mil2 / (copper_mm / _MIL) * _MIL


def _track_widths(board: Board, tree: PowerTree) -> list[LayoutFact]:
    facts = []
    for rail in tree.rails:
        budget = rail.budget
        if budget is None or budget.load_ma <= 0:
            continue
        tracks = [t for t in board.tracks if t.net == rail.net]
        if not tracks:
            continue
        amps = budget.load_ma / 1000

        def need(layer: str, amps: float = amps) -> float:
            return ipc2221_width_mm(amps, board.copper_mm(layer), board.is_outer(layer))

        worst = min(tracks, key=lambda t: t.width / need(t.layer))
        needed = need(worst.layer)
        flagged = worst.width < needed
        load = f"{'at least ' if budget.unknown else ''}{budget.load_ma:g} mA"
        um = board.copper_mm(worst.layer) * 1000
        text = (
            f"{short_net_name(rail.net)}: narrowest track {worst.width:g} mm on {worst.layer}; "
            f"IPC-2221 gives {needed:.2f} mm for its known load of {load} at "
            f"{RISE_C:g} °C rise with {um:g} µm copper, if that track carries all of it."
        )
        text += f" Narrower. {_MAY_FAIL}" if flagged else ""
        facts.append(
            LayoutFact(
                id="",
                kind="track_width",
                refs=list(rail.fed_by),
                pins=[],
                net=rail.net,
                value=worst.width,
                limit=round(needed, 2),
                unit="mm",
                flagged=flagged,
                text=text,
            )
        )
    return facts


def _ref_key(ref: str) -> tuple[str, int]:
    head = ref.rstrip("0123456789")
    tail = ref[len(head) :]
    return head, int(tail) if tail else 0
