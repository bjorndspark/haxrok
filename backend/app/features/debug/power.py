"""The board's power tree, built from the netlist: the sources at its roots
(USB VBUS, a battery, an input nothing on the board makes), the parts that
make one rail from another, and each rail's voltage range.

A rail's voltage comes from, in order: its net's name (+3V3), a feedback
divider ``calc`` worked out, a fixed voltage in the regulator's value
(AMS1117-3.3, RT9013-33GB), the output voltage in its datasheet facts, and
last the rails feeding it (a power mux's VDD is VBUS or the battery). A
battery's range is the user's when the review form gives one, else
inferred: the charger's regulation voltage, the net's name, or the Li-ion
default, 3.0-4.2 V.

Pure functions over the parsed design, no I/O.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.features.debug.calc import is_ground, named_voltage
from app.features.debug.design import Design, is_inductor, short_net_name
from app.features.debug.schemas import (
    Calculation,
    LimitFact,
    PowerPart,
    PowerPartKind,
    PowerRail,
    PowerSourceKind,
    PowerTree,
    VoltageBasis,
)

Range = tuple[float, float]

LIION = (3.0, 4.2)  # a single Li-ion/LiPo cell, empty to full
_USB = 5.0

_IC_REF = re.compile(r"(U|IC)\d+", re.IGNORECASE)
# Pin names, after ``_norm``. VREG_IN/VREG_VOUT: an MCU's internal regulator.
_IN_PIN = re.compile(r"(VREG)?P?V?IN\d*|VI")
_OUT_PIN = re.compile(r"(VREG)?V?OUT\d*|VO")
_SW_PIN = re.compile(r"LX\d*|SW\d*")
_CHARGER_IN = re.compile(r"VDD|VCC|V?IN\d*|VBUS|VCHG|USB|AC|DC")
_CHARGER_BAT = re.compile(r"V?BATT?\d*|BATP")
_CHARGER_CTL = re.compile(r"PROG|ISET\d*|STAT\d*|CHRG|STDBY|ICHG|CHG|TERM|IPRE|ITERM")
# Rails by name: VDD, VIN, VSYS, +BATT, RAW, PWR_IN. Checked on the short name.
_POWER_NAME = re.compile(
    r"^\+?(V(CC|DD|IN|SYS|BUS|BATT?|MAIN|RAW|PWR|USB|EXT|CHG|M\b)|RAW|PWR|BATT?|VCHG|SYS)",
    re.IGNORECASE,
)
_BATTERY_NAME = re.compile(r"^\+?V?BATT?\+?(_?(IN|P|POS|RAW))?$", re.IGNORECASE)
_USB_NAME = {"VBUS", "USB_VBUS", "VUSB"}
_LOAD_PIN = re.compile(r"^(VDD\w*|VCC\w*|IOVDD|VDDIO|DVDD|AVDD|3V3|VM|VBAT|V\+)$", re.IGNORECASE)
_ANODE = re.compile(r"A\d?|ANODE", re.IGNORECASE)
_CATHODE = re.compile(r"K\d?|CATHODE", re.IGNORECASE)
_CONNECTOR = {"J", "USB", "P", "CN"}
_DIODE_SKIP = re.compile(r"LED|TVS|ZENER|ESD|WS28|SK68", re.IGNORECASE)
_SWITCH_LIB = re.compile(r"SW_|SWITCH|SPDT|SPST|DPDT|SLIDE|TOGGLE", re.IGNORECASE)
# A button only closes while pressed: never a power path.
_BUTTON = re.compile(r"PUSH|BUTTON|TACT|KEY", re.IGNORECASE)
_ZERO_OHM = re.compile(r"0(R|Ω|OHM)?", re.IGNORECASE)
# A fixed regulator's voltage in its value: "-3.3", "-3V3", "-33GB" (3.3), "-5".
_FIXED = re.compile(r"[-_](\d{1,2})(?:[.V](\d{1,2}))?(?=[A-Z]|$|[-_ /])")
# The review form's battery field: "3.7", "3.0-4.2 V", "6 to 8.4".
_NUMBER = r"(\d+(?:\.\d+)?)\s*V?"
_BATTERY_TEXT = re.compile(rf"{_NUMBER}(?:\s*(?:-|–|—|~|to)\s*{_NUMBER})?", re.IGNORECASE)
# Datasheet facts.
_VOUT_FACT = re.compile(r"output\s+voltage|^V_?OUT\b", re.IGNORECASE)
_NOT_VOUT = re.compile(
    r"accura|toleran|range|ripple|regulation|drop|noise|line|load|transient|discharge|"
    r"threshold|protect|power[\s-]*good|adjust",
    re.IGNORECASE,
)
_CHARGE_FACT = re.compile(
    r"(regulat|charge|float|battery|termination)\w*\s+(\w+\s+)?voltage|^V_?(REG|BAT|FLOAT|CV)\b",
    re.IGNORECASE,
)
_NOT_CHARGE = re.compile(
    r"threshold|uvlo|pre-?charge|recharge|detect|protect|over|under|short|hyster|input|"
    r"tolerance|accura",
    re.IGNORECASE,
)


_Pins = Mapping[str, list[tuple[str, str]]]


@dataclass(frozen=True)
class _Volts:
    range: Range | None
    basis: VoltageBasis
    note: str
    inferred: bool


@dataclass(frozen=True)
class _Edge:
    ref: str
    kind: PowerPartKind
    inputs: tuple[str, ...]
    output: str


def build(
    design: Design,
    calculations: Sequence[Calculation] = (),
    limits: Mapping[str, Sequence[LimitFact]] | None = None,
    battery: Range | None = None,
) -> PowerTree:
    """The power tree of ``design``. ``calculations`` are ``calc.lookup``'s
    (feedback-divider outputs); ``limits`` the datasheet facts by ref, when
    they're read; ``battery`` the user's (min, max) for every battery."""
    limits = limits or {}
    index = _pin_index(design)
    usb = {net for net in design.nets if _is_usb(design, net)}
    battery_nets = {net for net in design.nets if _is_battery(design, index, net)}
    edges = _edges(design, index, usb | battery_nets)
    battery_nets |= {e.output for e in edges if e.kind == "charger"}
    produced = {e.output for e in edges if e.kind != "charger"}
    rails = {e.output for e in edges} | {n for e in edges for n in e.inputs} | usb | battery_nets
    sources: dict[str, PowerSourceKind] = {}
    for net in rails - produced:
        sources[net] = "usb" if net in usb else "battery" if net in battery_nets else "input"
    bat = _battery(edges, limits, battery, sources)
    resolved: dict[str, _Volts] = {}

    def voltage(net: str, seen: frozenset[str]) -> _Volts:
        if net not in resolved:
            resolved[net] = _rail_voltage(net, seen)
        return resolved[net]

    def _rail_voltage(net: str, seen: frozenset[str]) -> _Volts:
        kind = sources.get(net)
        if kind == "usb":
            return _Volts((_USB, _USB), "usb", "USB VBUS", False)
        if kind == "battery":
            return bat
        if (v := named_voltage(net)) is not None:
            return _Volts((v, v), "named", "the net's name", False)
        makers = [e for e in edges if e.output == net and e.kind != "charger"]
        regulators = [e for e in makers if e.kind == "regulator"]
        for e in regulators:
            found = _regulated(design, e.ref, calculations, limits.get(e.ref, ()))
            if found is not None:
                return found
        if regulators:
            return _Volts(None, "unknown", f"{regulators[0].ref}'s output", False)
        ups = [
            (e, up, voltage(up, seen | {net}))
            for e in makers
            for up in e.inputs
            if up not in seen and up != net
        ]
        known = [(e, up, v) for e, up, v in ups if v.range is not None]
        if known:
            lo = min(v.range[0] for _, _, v in known if v.range)
            hi = max(v.range[1] for _, _, v in known if v.range)
            via = ", ".join(sorted({f"{short_net_name(up)} via {e.ref}" for e, up, _ in known}))
            if any(_head(e.ref) == "D" for e, _, _ in known):
                via += ", less the diode's drop"
            return _Volts((lo, hi), "upstream", via, any(v.inferred for _, _, v in known))
        if net in battery_nets:
            return bat
        return _Volts(None, "unknown", "", False)

    out: list[PowerRail] = []
    for net in _ordered(rails, edges, sources):
        v = voltage(net, frozenset())
        makers = sorted({e.ref for e in edges if e.output == net}, key=_ref_key)
        out.append(
            PowerRail(
                net=net,
                source=sources.get(net),
                min=round(v.range[0], 3) if v.range else None,
                max=round(v.range[1], 3) if v.range else None,
                basis=v.basis,
                inferred=v.inferred,
                note=v.note,
                fed_by=makers,
                loads=_loads(design, net, set(makers), edges),
            )
        )
    parts = [
        PowerPart(
            ref=e.ref,
            value=design.parts[e.ref].value if e.ref in design.parts else "",
            kind=e.kind,
            inputs=list(e.inputs),
            output=e.output,
        )
        for e in sorted(edges, key=lambda e: (_ref_key(e.ref), e.output))
    ]
    return PowerTree(rails=out, parts=parts, battery_override=battery is not None)


def format_power(tree: PowerTree | None) -> str:
    """The tree as the prompt's "Power tree" section: one line per part,
    then one per rail, roots first."""
    if tree is None or not tree.rails:
        return "(no power rails found: no regulator, power switch or pass part)\n"
    lines = ["Parts (inputs -> output):"]
    lines += [
        f"- {p.ref} {p.value} ({_KIND_TEXT[p.kind]}): {', '.join(p.inputs)} -> {p.output}"
        for p in tree.parts
    ]
    lines.append("Rails (roots first):")
    for r in tree.rails:
        head = f"- {r.net}"
        if r.source:
            head += f" [source: {r.source}]"
        head += f": {volts(r.min, r.max)}"
        why = [r.basis + (f", {r.note}" if r.note else "")]
        if r.inferred:
            why.append("inferred, check it")
        head += f" ({'; '.join(why)})"
        if r.fed_by:
            head += f"; made by {', '.join(r.fed_by)}"
        if r.loads:
            head += f"; supplies {', '.join(r.loads)}"
        lines.append(head)
    return "\n".join(lines) + "\n"


def volts(lo: float | None, hi: float | None) -> str:
    """ "3.3 V", "3.0-4.2 V", or "voltage unknown"."""
    if lo is None or hi is None:
        return "voltage unknown"
    if abs(hi - lo) < 1e-9:
        return f"{lo:g} V"
    return f"{lo:g}-{hi:g} V"


def parse_battery(text: str) -> Range | None:
    """The review form's battery field: "3.7" or "3.0-4.2" (an en dash,
    "~" or "to" works too, and a trailing V) -> (min, max); None when blank.
    Raises ValueError when it isn't a voltage or a range of two, low to
    high, within 0-60 V."""
    text = text.strip()
    if not text:
        return None
    m = _BATTERY_TEXT.fullmatch(text)
    if not m:
        raise ValueError(f"not a voltage: {text!r}")
    lo = float(m[1])
    hi = float(m[2]) if m[2] else lo
    if not 0 < lo <= hi <= 60:
        raise ValueError(f"not a battery range: {text!r}")
    return lo, hi


_KIND_TEXT: dict[PowerPartKind, str] = {
    "regulator": "regulator",
    "switch": "power mux/switch",
    "charger": "charger",
    "pass": "pass part",
}


# --- the parts ---------------------------------------------------------------


def _edges(design: Design, pins: _Pins, roots: set[str]) -> list[_Edge]:
    """Every part that moves power from one rail to another. Grown from the
    rails known by name or as sources, so an op-amp's IN/OUT pins on signal
    nets never count. A part found again with more inputs (a rail that
    turned out to be powered) replaces its first edge."""
    power = {n for n in design.nets if not is_ground(n) and _named_power(n)} | roots
    edges: dict[tuple[str, str], _Edge] = {}
    changed = True
    while changed:
        changed = False
        current = list(edges.values())
        for edge in (*_ic_edges(design, pins, power), *_pass_edges(design, pins, power, current)):
            key = (edge.ref, edge.output)
            if edges.get(key) == edge:
                continue
            if key in edges and set(edge.inputs) <= set(edges[key].inputs):
                continue
            edges[key] = edge
            power.add(edge.output)
            changed = True
    return list(edges.values())


def _ic_edges(design: Design, index: _Pins, power: set[str]) -> list[_Edge]:
    """Regulators, power muxes and chargers: ICs with an input pin on a
    powered rail and an output pin (or a switch pin and its inductor) on a
    rail or on another IC's supply pin."""
    found: list[_Edge] = []
    for ref in sorted(design.parts, key=_ref_key):
        if not _IC_REF.fullmatch(ref):
            continue
        pins = index.get(ref, [])
        names = {_norm(fn) for fn, _ in pins}
        bats = sorted({net for fn, net in pins if _CHARGER_BAT.fullmatch(_norm(fn))})
        if bats and any(_CHARGER_CTL.fullmatch(n) for n in names):
            ins = sorted(
                {n for fn, n in pins if _CHARGER_IN.fullmatch(_norm(fn)) and n in power} - set(bats)
            )
            if ins:
                found += [_Edge(ref, "charger", tuple(ins), b) for b in bats]
                outs = sorted({n for fn, n in pins if _OUT_PIN.fullmatch(_norm(fn))} - set(bats))
                found += [_Edge(ref, "switch", (*ins, *bats), o) for o in outs if _rail(o)]
                continue
        # In pin-name order, so a mux lists IN1 before IN2.
        ins = list(dict.fromkeys(n for fn, n in sorted(pins) if _is_in(fn) and n in power))
        if not ins:
            continue
        made = {n for fn, n in pins if _OUT_PIN.fullmatch(_norm(fn))}
        for fn, sw in pins:
            if _SW_PIN.fullmatch(_norm(fn)):
                made |= _through_inductor(design, index, sw)
        made = {o for o in made if _rail(o) and o not in ins}
        made = {o for o in made if o in power or _feeds_loads(design, o, ref)}
        kind: PowerPartKind = "switch" if len(ins) > 1 and len(made) == 1 else "regulator"
        found += [_Edge(ref, kind, tuple(ins), o) for o in sorted(made)]
    return found


def _pass_edges(design: Design, index: _Pins, power: set[str], edges: list[_Edge]) -> list[_Edge]:
    """Diodes (anodes to cathode, so a BAT54C ORs two rails), and fuses,
    ferrites, 0 Ω links and slide switches between two rails, oriented from
    the side nearer a source."""
    on_tree = {e.output for e in edges} | {n for e in edges for n in e.inputs}
    found: list[_Edge] = []
    for ref, part in sorted(design.parts.items(), key=lambda kv: _ref_key(kv[0])):
        head = _head(ref)
        lib = part.lib_part.rsplit(":", 1)[-1]
        pins = index.get(ref, [])
        if head == "D":
            if _DIODE_SKIP.search(f"{lib} {part.value}"):
                continue
            anodes = sorted({n for fn, n in pins if _ANODE.fullmatch(fn) and n in power})
            cathodes = {n for fn, n in pins if _CATHODE.fullmatch(fn) and _rail(n)}
            found += [
                _Edge(ref, "pass", tuple(a for a in anodes if a != k), k)
                for k in sorted(cathodes)
                if any(a != k for a in anodes)
            ]
            continue
        nets = sorted({n for _, n in pins if _rail(n)})
        if (
            len(nets) != 2
            or any(is_ground(n) for _, n in pins)
            or not _passive(head, lib, part.value)
        ):
            continue
        a, b = nets
        if a not in power | on_tree and b not in power | on_tree:
            continue
        rank_a, rank_b = _rank(design, index, a, edges), _rank(design, index, b, edges)
        if b not in power | on_tree or rank_a > rank_b:
            found.append(_Edge(ref, "pass", (a,), b))
        elif a not in power | on_tree or rank_b > rank_a:
            found.append(_Edge(ref, "pass", (b,), a))
    return found


def _passive(head: str, lib: str, value: str) -> bool:
    """A part power passes through unchanged: a fuse, a ferrite bead, a
    0 Ω link or a slide/toggle switch (not a push button)."""
    text = f"{lib} {value}"
    if head in {"F", "FB"} or re.search(r"fuse|ferrite", text, re.IGNORECASE):
        return True
    if head == "R":
        return _ZERO_OHM.fullmatch(value.strip()) is not None
    switch = head in {"SW", "S", "PWR"} or _SWITCH_LIB.search(lib) is not None
    return switch and _BUTTON.search(text) is None


def _rank(design: Design, index: _Pins, net: str, edges: list[_Edge]) -> int:
    """How near a source a rail is, to orient a pass part between two:
    2 for a rail with a battery or USB connector on it, or one a regulator
    or switch makes; 1 for one named like a source (VBUS, +BATT); else 0."""
    if any(e.output == net and e.kind != "charger" for e in edges):
        return 2
    if _battery_part_on(design, index, net) or any(
        _norm(n.function) == "VBUS" and _head(n.ref) in _CONNECTOR for n in design.nets.get(net, [])
    ):
        return 2
    return 1 if _is_usb(design, net) or _is_battery(design, index, net) else 0


def _through_inductor(design: Design, index: _Pins, sw: str) -> set[str]:
    """The far ends of the inductors on a switch node (a buck's output)."""
    far: set[str] = set()
    for node in design.nets.get(sw, []):
        if is_inductor(design, node.ref):
            far |= {n for _, n in index.get(node.ref, []) if n != sw}
    return far


def _feeds_loads(design: Design, net: str, maker: str) -> bool:
    return any(n.ref != maker and _LOAD_PIN.match(n.function) for n in design.nets.get(net, []))


def _loads(design: Design, net: str, makers: set[str], edges: list[_Edge]) -> list[str]:
    takers = {e.ref for e in edges if net in e.inputs}
    refs = {
        n.ref
        for n in design.nets.get(net, [])
        if n.ref not in makers and _IC_REF.fullmatch(n.ref) and _LOAD_PIN.match(n.function)
    }
    return sorted(refs | (takers - makers), key=_ref_key)


# --- voltages ----------------------------------------------------------------


def _regulated(
    design: Design, ref: str, calculations: Sequence[Calculation], limits: Sequence[LimitFact]
) -> _Volts | None:
    """A regulator's output: its feedback divider, the voltage in its value,
    or its datasheet's output voltage; None when none of them gives one."""
    for c in calculations:
        if c.kind == "regulator_output" and c.ic == ref and c.value is not None:
            return _Volts((c.value, c.value), "calculated", f"{ref} feedback ({c.id})", False)
    part = design.parts.get(ref)
    if part and (v := fixed_voltage(part.value)) is not None:
        return _Volts((v, v), "fixed", f"{ref}'s value {part.value}", False)
    for lim in limits:
        if not _VOUT_FACT.search(lim.parameter) or _NOT_VOUT.search(lim.parameter):
            continue
        typ = to_volts(lim.typ, lim.unit)
        lo, hi = to_volts(lim.min, lim.unit), to_volts(lim.max, lim.unit)
        if typ is None and lo is not None and hi is not None and hi - lo <= 0.1 * hi:
            typ = (lo + hi) / 2
        if typ is not None and typ > 0:
            return _Volts((typ, typ), "datasheet", f"{ref} datasheet p.{lim.page}", False)
    return None


def fixed_voltage(value: str) -> float | None:
    """The output voltage a fixed regulator's value names: "AMS1117-3.3",
    "AMS1117-3V3" → 3.3; "RT9013-33GB" → 3.3 (two digits are tenths);
    "LM1117-5.0" → 5. None without one (TPS63070RNMR, AMS1117-ADJ)."""
    m = _FIXED.search(value.upper())
    if not m:
        return None
    whole, frac = m.groups()
    if frac:
        v = float(f"{whole}.{frac}")
    elif len(whole) == 2:
        v = int(whole) / 10
    else:
        v = float(whole)
    return v if 0.5 <= v <= 24 else None


def _battery(
    edges: list[_Edge],
    limits: Mapping[str, Sequence[LimitFact]],
    override: Range | None,
    sources: Mapping[str, PowerSourceKind],
) -> _Volts:
    """One range for every battery on the board: the user's, else the
    charger's regulation voltage, else a voltage in the net's name, else
    the Li-ion default."""
    if override is not None:
        return _Volts(override, "user", "your value on the review form", False)
    for e in (e for e in edges if e.kind == "charger"):
        for lim in limits.get(e.ref, ()):
            if not _CHARGE_FACT.search(lim.parameter) or _NOT_CHARGE.search(lim.parameter):
                continue
            v = to_volts(lim.typ if lim.typ is not None else lim.max, lim.unit)
            if v is not None and 3.4 <= v <= 4.45:
                low = 2.5 if v < 3.8 else LIION[0]  # LiFePO4 charges to 3.6 V
                note = f"{e.ref} charge voltage, datasheet p.{lim.page}"
                return _Volts((low, v), "charger", note, True)
    for net, kind in sorted(sources.items()):
        if kind == "battery" and (v := named_voltage(net)) is not None:
            cells = round(v / 3.7)
            if cells >= 1 and abs(v - 3.7 * cells) <= 0.15 * cells:
                rng = (LIION[0] * cells, LIION[1] * cells)
                note = f"{cells}-cell Li-ion, from the name {short_net_name(net)}"
                return _Volts(rng, "default", note, True)
            return _Volts((v, v), "named", "the net's name", True)
    return _Volts(LIION, "default", "Li-ion default; set it on the review form", True)


def to_volts(value: float | None, unit: str) -> float | None:
    """A datasheet value in volts, from "V" or "mV"; None for other units."""
    if value is None:
        return None
    u = unit.strip()
    if u == "V":
        return value
    if u == "mV":
        return value / 1000
    return None


# --- helpers -----------------------------------------------------------------


def _ordered(rails: set[str], edges: list[_Edge], sources: Mapping[str, object]) -> list[str]:
    """Roots first, then each rail after the rails that feed it (by depth),
    then by name."""
    depth: dict[str, int] = {}

    def level(net: str, seen: frozenset[str]) -> int:
        if net in depth:
            return depth[net]
        ups = [u for e in edges if e.output == net and e.kind != "charger" for u in e.inputs]
        ups = [u for u in ups if u not in seen]
        d = 0 if net in sources or not ups else 1 + max(level(u, seen | {net}) for u in ups)
        depth[net] = d
        return d

    return sorted(rails, key=lambda n: (level(n, frozenset()), short_net_name(n)))


def _is_usb(design: Design, net: str) -> bool:
    """VBUS by name, or a USB connector's VBUS pin."""
    if short_net_name(net).lstrip("+").upper() in _USB_NAME:
        return True
    return any(
        _norm(n.function) == "VBUS" and _head(n.ref) in _CONNECTOR for n in design.nets.get(net, [])
    )


def _is_battery(design: Design, index: _Pins, net: str) -> bool:
    """+BATT/VBAT by name, or the live side of a battery or its connector."""
    if is_ground(net):
        return False
    return _BATTERY_NAME.fullmatch(short_net_name(net)) is not None or _battery_part_on(
        design, index, net
    )


def _battery_part_on(design: Design, index: _Pins, net: str) -> bool:
    # Two pins at most, so a protection IC named "battery …" doesn't count.
    for n in design.nets.get(net, []):
        part = design.parts.get(n.ref)
        if part is None or len(index.get(n.ref, [])) > 2:
            continue
        text = f"{part.value} {part.lib_part}"
        if _head(n.ref) in {"BT", "BAT"} or re.search(r"batt", text, re.IGNORECASE):
            return True
    return False


def _named_power(net: str) -> bool:
    return named_voltage(net) is not None or _POWER_NAME.match(short_net_name(net)) is not None


def _rail(net: str) -> bool:
    return not is_ground(net) and not net.startswith("unconnected-")


def _is_in(function: str) -> bool:
    # "IN+"/"-IN" are an op-amp's inputs, not a supply.
    return not re.search(r"[+\-]", function) and _IN_PIN.fullmatch(_norm(function)) is not None


def _pin_index(design: Design) -> _Pins:
    """(pin name, net) for each connected pin, by ref."""
    index: dict[str, list[tuple[str, str]]] = {}
    for net, nodes in design.nets.items():
        for n in nodes:
            index.setdefault(n.ref, []).append((n.function, net))
    return index


def _norm(pin: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", pin.upper())


def _head(ref: str) -> str:
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


def _ref_key(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)
