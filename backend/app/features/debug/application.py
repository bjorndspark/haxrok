"""The board checked against each datasheet's typical application circuit.

``facts.FactReader`` copies every IC's typical application out of its
datasheet: each external part's kind, role, which IC pins it joins, the
range of values the datasheet allows and whether it is required. This
module compares that with the IC's neighbourhood in the netlist, with no
model call, and reports three kinds of deviation, numbered ``dv1``, …:

- **open pin**: an IC pin a required part connects to is on no net with
  anything else.
- **missing**: no board part of the same kind joins the IC pins' nets (with
  one IC pin: none on that pin's net), directly or, for a part that isn't a
  resistor, through one series resistor. A pull resistor isn't missing when
  the board ties its pin straight to a rail.
- **out of range**: a present part's value is outside the datasheet's range.
  Capacitors in parallel are summed, and only a total below the minimum
  counts: datasheets give effective (derated) capacitance, which the
  nominal value always exceeds. A part with only a typical value is never
  flagged for its value.

Inductors are also read by their part number: an "R as decimal point" code
in the MPN or library part (``2R2`` = 2.2 µH, ``R47`` = 0.47 µH) is the
part that gets fitted, so it's what the range check uses, and a value field
that disagrees with it is reported. Plain 3-digit codes (``100``) are
ambiguous and ignored.

Parts of kind ``other`` aren't checked, and neither is an application part
with no IC pin on the board or one spanning more than two nets (a 2-pin
part can't join them all, so presence can't be told). Presence is lenient
on purpose: any part of the kind on those nets counts, so a deviation is
always a real one.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.features.debug.calc import (
    format_farads,
    format_ohms,
    is_ground,
    named_voltage,
    parse_farads,
    parse_ohms,
)
from app.features.debug.design import Design, Part, is_inductor, short_net_name
from app.features.debug.schemas import AppPart, AppPartKind

# Enough to cover every IC of a typical board, few enough that an MCU whose
# datasheet lists a capacitor per supply pin doesn't drown the prompt.
_MAX = 12

# Relative slack for float noise when comparing with a range ("4.7 µF" vs 4.7).
_EPS = 1e-6


@dataclass(frozen=True)
class Deviation:
    """One way the board departs from a datasheet's typical application."""

    id: str  # "dv2"
    ref: str  # the IC
    page: int  # the datasheet page that says so
    parts: tuple[str, ...]  # board parts involved, "" none (missing)
    text: str


def check(design: Design, application: Mapping[str, Iterable[AppPart]]) -> list[Deviation]:
    """Every deviation on the board, IC by IC in ``application``'s order,
    ids dv1…, at most ``_MAX``; see the module docstring."""
    nets_of = _nets_by_part(design)
    found: list[Deviation] = []
    open_pins: set[str] = set()
    for ref, parts in application.items():
        if ref not in design.parts:
            continue
        for a in parts:
            found += _one(design, nets_of, ref, a, open_pins)
    return [
        Deviation(f"dv{i}", d.ref, d.page, d.parts, d.text)
        for i, d in enumerate(found[:_MAX], start=1)
    ]


def format_deviations(deviations: list[Deviation]) -> str:
    """The deviations as the model reads them."""
    if not deviations:
        return (
            "(none: every required part of the typical applications read is on the board, "
            "and every value with a datasheet range is within it)\n"
        )
    return "\n".join(f"{d.id}: {d.text}" for d in deviations) + "\n"


def _one(
    design: Design, nets_of: dict[str, set[str]], ref: str, a: AppPart, open_pins: set[str]
) -> list[Deviation]:
    """The deviations for one application part of IC ``ref``."""
    if a.kind == "other":
        return []
    pin_nets = {pin: net for pin in a.pins if (net := design.net_of(f"{ref}.{pin}"))}
    targets = set(pin_nets.values())
    if not targets or len(targets) > 2:
        return []
    ic = f"{ref} ({design.parts[ref].value})"
    what = f"{a.role} ({a.kind}{f' {a.value}' if a.value else ''})"
    if a.required:
        open_ = [p for p, net in pin_nets.items() if all(n.ref == ref for n in design.nets[net])]
        # One deviation per open pin, however many parts the datasheet ties to it.
        alone = [pin for pin in open_ if f"{ref}.{pin}" not in open_pins]
        if open_ and not alone:
            return []
        if alone:
            open_pins.update(f"{ref}.{pin}" for pin in alone)
            pins = ", ".join(_pin(design, ref, pin) for pin in alone)
            return [
                Deviation(
                    "",
                    ref,
                    a.page,
                    (),
                    f"{ic}: {pins} connects to nothing, but the datasheet's typical "
                    f"application ties it to {_a(what)} (p.{a.page}).",
                )
            ]
    # A non-resistor part may reach an IC pin through one series resistor
    # (RP2040's crystal on XOUT via 1 kΩ, an RC compensation network).
    reach = {
        t: {t} if a.kind == "resistor" else {t} | _through_resistor(design, nets_of, t)
        for t in targets
    }
    present = [
        p
        for r, p in design.parts.items()
        if r != ref
        and _kind(design, p) == a.kind
        and all(nets & nets_of.get(r, set()) for nets in reach.values())
    ]
    if not present:
        if not a.required or _tied_to_rail(a, targets):
            return []
        on = " and ".join(f"{_pin(design, ref, pin)} on {net}" for pin, net in pin_nets.items())
        return [
            Deviation(
                "",
                ref,
                a.page,
                (),
                f"{ic}: no {a.kind} joins {on}, but the datasheet's typical application "
                f"has {_a(what)} there (p.{a.page}) and doesn't call it optional.",
            )
        ]
    return _values(design, nets_of, ic, ref, a, present)


def _values(
    design: Design,
    nets_of: dict[str, set[str]],
    ic: str,
    ref: str,
    a: AppPart,
    present: list[Part],
) -> list[Deviation]:
    """Out-of-range values among the parts present, and inductors whose
    value field disagrees with their part number."""
    found: list[Deviation] = []
    actual: dict[str, float] = {}  # ref -> value in base units, as fitted
    for p in present:
        value = _value(a.kind, p)
        if a.kind == "inductor" and (code := _inductor_code(p)) is not None:
            if value is not None and abs(value - code) > 0.05 * max(value, code):
                found.append(
                    Deviation(
                        "",
                        ref,
                        a.page,
                        (p.ref,),
                        f"{p.ref}'s value field says {_format(a.kind, value)} but its part "
                        f"number ({_code_source(p)}) codes {_format(a.kind, code)}; the "
                        f"fitted part is the part number's. It is {ic}'s {a.role} (p.{a.page}).",
                    )
                )
            value = code
        if value is not None:
            actual[p.ref] = value
    scale = _scale(a.unit, a.kind)
    if scale is None or (a.min is None and a.max is None) or not actual:
        return found
    lo = a.min * scale if a.min is not None else None
    hi = a.max * scale if a.max is not None else None
    allowed = _range_text(a)
    if a.kind == "capacitor":
        # Parallel capacitors (same two nets) add up; the best group counts.
        groups: dict[frozenset[str], list[str]] = {}
        for r in actual:
            groups.setdefault(frozenset(nets_of[r]), []).append(r)
        refs, total = max(
            ((rs, sum(actual[r] for r in rs)) for rs in groups.values()), key=lambda g: g[1]
        )
        if lo is not None and total < lo * (1 - _EPS):
            names = " + ".join(refs)
            found.append(
                Deviation(
                    "",
                    ref,
                    a.range_page or a.page,
                    tuple(refs),
                    f"{ic}: {a.role} {names} is {format_farads(total)} nominal, below the "
                    f"datasheet's {allowed} (p.{a.range_page or a.page}), which is the "
                    "effective capacitance after DC-bias derating.",
                )
            )
        return found
    if any(_within(v, lo, hi) for v in actual.values()):
        return found
    values = ", ".join(f"{r} {_format(a.kind, v)}" for r, v in actual.items())
    found.append(
        Deviation(
            "",
            ref,
            a.range_page or a.page,
            tuple(actual),
            f"{ic}: {a.role} {values} is outside the datasheet's {allowed} "
            f"(p.{a.range_page or a.page}).",
        )
    )
    return found


# --- board parts ------------------------------------------------------------------


def _nets_by_part(design: Design) -> dict[str, set[str]]:
    nets: dict[str, set[str]] = {}
    for name, nodes in design.nets.items():
        for n in nodes:
            nets.setdefault(n.ref, set()).add(name)
    return nets


def _through_resistor(design: Design, nets_of: dict[str, set[str]], net: str) -> set[str]:
    """The nets a 2-pin resistor joins to ``net``."""
    return {
        other
        for n in design.nets[net]
        if n.ref in design.parts
        and len(nets := nets_of.get(n.ref, set())) == 2
        and _kind(design, design.parts[n.ref]) == "resistor"
        for other in nets - {net}
    }


def _kind(design: Design, part: Part) -> AppPartKind | None:
    """A board part's kind, by designator or symbol (KiCad's Device:R_Small,
    Device:Crystal; a crystal is sometimes numbered U10)."""
    head = part.ref.rstrip("0123456789").upper()
    lib = part.lib_part.rsplit(":", 1)[-1].upper()
    if is_inductor(design, part.ref):
        return "inductor"
    if head in {"Y", "X", "XTAL"} or lib.startswith(("CRYSTAL", "RESONATOR")):
        return "crystal"
    if head == "C" or lib in {"C", "CP"} or lib.startswith(("C_", "CP_")):
        return "capacitor"
    if head == "R" or lib == "R" or lib.startswith("R_"):
        return "resistor"
    if head == "D" or lib in {"D", "LED"} or lib.startswith(("D_", "LED_")):
        return "diode"
    return None


# "SUPPLY" names a rail: +3V3, VCC, VDD_IO, VIN, VBAT, VSYS, VBUS.
_RAIL = re.compile(r"\+|V(CC|DD|IN|BAT|SYS|BUS)", re.IGNORECASE)


def _tied_to_rail(a: AppPart, targets: set[str]) -> bool:
    """A pull resistor's pin tied straight to a rail does the resistor's job."""
    if a.kind != "resistor" or "pull" not in a.role.lower():
        return False
    return any(
        is_ground(net) or named_voltage(net) is not None or _RAIL.match(short_net_name(net))
        for net in targets
    )


def _a(noun: str) -> str:
    return f"{'an' if noun[:1].lower() in 'aeio' else 'a'} {noun}"


def _pin(design: Design, ref: str, pin: str) -> str:
    name = design.pin_name(f"{ref}.{pin}")
    return f"{ref}.{pin} ({name})" if name else f"{ref}.{pin}"


# --- values -------------------------------------------------------------------------

# "2.2uH", "2u2", "470nH", "0.47 µH 3A"; m is milli here (no one writes mega-henries).
_HENRIES = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([pnuµμm])(\d*)H?\b", re.IGNORECASE)
_HENRY_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3}

# An "R as decimal point" inductance code in µH: 2R2, 1R0, R47. Digits on
# both sides are bounded so "SRR1260" or "0R47" inside a longer number don't
# read as a code.
_R_CODE = re.compile(r"(?<![0-9])([0-9])R([0-9]{1,2})(?![0-9])|(?<![0-9A-Z])R([0-9]{2})(?![0-9])")

_PREFIX = {
    "": 1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
}
_BASE: dict[AppPartKind, str] = {"resistor": "Ω", "capacitor": "F", "inductor": "H"}


def parse_henries(value: str) -> float | None:
    """An inductor's value field in henries ("2.2uH", "2u2"); None otherwise."""
    m = _HENRIES.match(value)
    if not m:
        return None
    whole, mult, frac = m.groups()
    if frac and "." in whole:
        return None
    key = mult if mult in {"µ", "μ", "m"} else mult.lower()
    return float(f"{whole}.{frac}" if frac else whole) * _HENRY_MULT[key]


def _value(kind: AppPartKind, part: Part) -> float | None:
    match kind:
        case "resistor":
            return parse_ohms(part.value)
        case "capacitor":
            return parse_farads(part.value)
        case "inductor":
            return parse_henries(part.value)
        case _:
            return None


def _inductor_code(part: Part) -> float | None:
    """The µH code in the part number or library part, in henries; None
    when there is none, or two that disagree."""
    codes = set()
    for text in (part.mpn, part.lib_part):
        for m in _R_CODE.finditer(text.upper()):
            whole, frac, only = m.groups()
            codes.add(float(f"{whole}.{frac}") if whole else float(f"0.{only}"))
    return codes.pop() * 1e-6 if len(codes) == 1 else None


def _code_source(part: Part) -> str:
    return part.mpn if _R_CODE.search(part.mpn.upper()) else part.lib_part.rsplit(":", 1)[-1]


def _scale(unit: str, kind: AppPartKind) -> float | None:
    """The factor from the datasheet's unit to base units, when the unit fits
    the kind ("µF" for a capacitor → 1e-6); None otherwise."""
    base = _BASE.get(kind)
    text = re.sub(r"ohms?$", "Ω", unit.strip(), flags=re.IGNORECASE)
    if base is None or not text.endswith(base):
        return None
    return _PREFIX.get(text[: -len(base)].strip())


def _within(value: float, lo: float | None, hi: float | None) -> bool:
    return (lo is None or value >= lo * (1 - _EPS)) and (hi is None or value <= hi * (1 + _EPS))


def _range_text(a: AppPart) -> str:
    if a.min is not None and a.max is not None:
        return f"{a.min:g}–{a.max:g} {a.unit}"
    if a.min is not None:
        return f"minimum of {a.min:g} {a.unit}"
    return f"maximum of {a.max:g} {a.unit}"


def _format(kind: AppPartKind, value: float) -> str:
    match kind:
        case "resistor":
            return format_ohms(value)
        case "capacitor":
            return format_farads(value)
        case _:
            for unit, scale, least in (("mH", 1e-3, 1e-3), ("µH", 1e-6, 1e-7)):
                if value >= least:
                    return f"{value / scale:g} {unit}"
            return f"{value / 1e-9:g} nH"
