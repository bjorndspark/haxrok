"""Each capacitor's voltage rating and each resistor's power rating against
the board, from the schematic alone (no datasheet read).

- **Capacitor**: the rating comes from its ``Voltage`` field or its value
  ("10uF 16V", "100n/50V", "4u7 6V3"). The voltage across it is the
  power tree's highest voltage on one side, less the lowest on the other
  (ground is 0 V); both sides need a known range. Above the rating:
  flagged when neither rail's voltage is inferred, listed otherwise. Above
  80 % of the rating: listed as "may fail" (a common derating rule; an
  MLCC also loses much of its capacitance near its rating). A capacitor
  with no rating is listed only across 12 V or more, where a default
  10–16 V MLCC can fail; below that it's ignored.
- **Resistor**: the rating comes from its ``Power`` field, otherwise the
  usual thick-film rating for its package size, read from the footprint
  (0402 62.5 mW, 0603 100 mW, …); with neither it's ignored. Its power is
  V²/R with both ends on known rails, or I²R in series with an LED whose
  current ``calc.py`` worked out (``led_current``). Flagged and listed as
  for a capacitor.

Results are ``Calculation``s (kind ``part_rating``), numbered after the
others. Pure functions, no I/O.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.features.debug.calc import format_ohms, is_ground, parse_ohms
from app.features.debug.design import Design
from app.features.debug.power import volts
from app.features.debug.schemas import Calculation, PowerRail, PowerTree

# Above this share of its rating a part is listed as "may fail".
_DERATING = 0.8
# A capacitor with no rating is listed only across this many volts or more.
_UNRATED_FROM = 12.0
_EPS = 1e-9

# The usual thick-film chip resistor rating per imperial package size, W.
_PACKAGE_WATTS = {
    "0201": 0.05,
    "0402": 0.0625,
    "0603": 0.1,
    "0805": 0.125,
    "1206": 0.25,
    "1210": 0.5,
    "2010": 0.75,
    "2512": 1.0,
}
# The imperial size in a footprint name: "R_0402_1005Metric" → 0402. The
# metric size comes second, so the first match is the imperial one.
_PACKAGE = re.compile(r"(?<!\d)(" + "|".join(_PACKAGE_WATTS) + r")(?!\d)")

# "16V", "6.3V", "6V3", "16 V", "50VDC" anywhere in a value; the field may
# also be a bare number ("16").
_VOLTS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*V(\d*)(?:DC)?(?![A-Z])", re.IGNORECASE)
_BARE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*")
# "0.1W", "1/8W", "100mW", "0.25 W", or a bare number of watts.
_WATTS = re.compile(r"\s*(\d+(?:\.\d+)?)(?:/(\d+))?\s*(m?)W?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class _Across:
    """The highest voltage across a two-pin part, the nets at its ends and
    whether any of it is a guess."""

    volts: float
    nets: tuple[str, str]
    inferred: bool
    basis: str


def check(
    design: Design, tree: PowerTree, calculations: Sequence[Calculation], first_id: int = 1
) -> list[Calculation]:
    """One calculation per capacitor or resistor near or over its rating,
    flagged ones first, ids ``cf<first_id>``…; see the module docstring.
    ``calculations`` are the netlist's, for an LED's current."""
    rails = {r.net: r for r in tree.rails if r.min is not None and r.max is not None}
    led_ma = _led_currents(calculations)
    out: list[Calculation] = []
    for ref in sorted(design.parts, key=_ref_order):
        head = ref.rstrip("0123456789")
        if head == "C":
            got = _capacitor(design, rails, ref)
        elif head == "R":
            got = _resistor(design, rails, ref, led_ma.get(ref))
        else:
            continue
        if got is not None:
            out.append(got)
    out.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def capacitor_volts(design: Design, ref: str) -> float | None:
    """A capacitor's rated voltage from its ``Voltage`` field or its value;
    None when neither gives one."""
    part = design.parts[ref]
    if part.voltage:
        if m := _BARE.fullmatch(part.voltage):
            return float(m.group(1))
        if m := _VOLTS.search(part.voltage):
            return _volts_of(m)
    m = _VOLTS.search(part.value)
    return _volts_of(m) if m else None


def resistor_watts(design: Design, ref: str) -> tuple[float, str] | None:
    """A resistor's rated power and where it comes from: its ``Power``
    field, else its package's usual rating; None with neither."""
    part = design.parts[ref]
    if part.power and (m := _WATTS.fullmatch(part.power)):
        whole, denom, milli = m.groups()
        watts = float(whole) / (float(denom) if denom else 1.0) / (1000 if milli else 1)
        if watts > 0:
            return watts, f"its Power field, {part.power}"
    if m := _PACKAGE.search(part.footprint.rsplit(":", 1)[-1]):
        size = m.group(1)
        return _PACKAGE_WATTS[size], f"the usual rating for {size}"
    return None


def _volts_of(m: re.Match[str]) -> float:
    whole, frac = m.groups()
    return float(f"{whole}.{frac}" if frac and "." not in whole else whole)


def _capacitor(design: Design, rails: dict[str, PowerRail], ref: str) -> Calculation | None:
    across = _across(design, rails, ref)
    if across is None or across.volts <= _EPS:
        return None
    rating = capacitor_volts(design, ref)
    part = design.parts[ref]
    where = _where(ref, part.value, across)
    if rating is None:
        if across.volts < _UNRATED_FROM:
            return None
        text = (
            f"{where}: no voltage rating in its value or a Voltage field. Check it: a "
            f"default 10–16 V MLCC can fail at {across.volts:g} V."
        )
        return _calc(ref, across, across.volts, None, False, text)
    share = across.volts / rating
    if share <= _DERATING + _EPS:
        return None
    over = share > 1 + _EPS
    flagged = over and not across.inferred
    text = f"{where}: {across.volts:g} V is {share:.0%} of its {rating:g} V rating."
    if flagged:
        text += " Above the rating, the capacitor can fail short."
    elif over:
        text += " Not flagged: the rail's voltage is inferred; check it."
    else:
        text += (
            f" May fail: above {_DERATING:.0%} of the rating, where an MLCC loses much of "
            "its capacitance and its life shortens."
        )
    return _calc(ref, across, across.volts, rating if flagged else None, flagged, text)


def _resistor(
    design: Design, rails: dict[str, PowerRail], ref: str, led_ma: float | None
) -> Calculation | None:
    part = design.parts[ref]
    ohms = parse_ohms(part.value)
    rated = resistor_watts(design, ref)
    if ohms is None or ohms <= 0 or rated is None:
        return None
    watts_rated, source = rated
    across = _across(design, rails, ref)
    if across is not None:
        watts = across.volts**2 / ohms
        how = f"({across.volts:g} V)² / {format_ohms(ohms)}"
        where = _where(ref, part.value, across)
        inferred = across.inferred
    elif led_ma is not None:
        watts = (led_ma / 1000) ** 2 * ohms
        how = f"({led_ma:g} mA)² × {format_ohms(ohms)}, the LED's highest current"
        where = f"{ref} ({part.value}) in series with an LED"
        inferred = False
    else:
        return None
    share = watts / watts_rated
    if share <= _DERATING + _EPS:
        return None
    over = share > 1 + _EPS
    flagged = over and not inferred
    text = (
        f"{where}: {how} = {watts * 1000:.3g} mW, {share:.0%} of {watts_rated * 1000:g} mW "
        f"({source})."
    )
    if flagged:
        text += " Above the rating, the resistor overheats and can drift or open."
    elif over:
        text += " Not flagged: the rail's voltage is inferred; check it."
    else:
        text += f" May fail: above {_DERATING:.0%} of the rating it runs hot."
    pins = [f"{ref}.1", f"{ref}.2"]
    return Calculation(
        id="",
        kind="part_rating",
        ic=ref,
        pins=pins,
        value=round(watts * 1000, 3),
        unit="mW",
        expected=watts_rated * 1000 if flagged else None,
        flagged=flagged,
        text=text,
    )


def _across(design: Design, rails: dict[str, PowerRail], ref: str) -> _Across | None:
    """The highest voltage across a two-pin part whose two ends are on
    ground or a power-tree rail with a known range; None otherwise."""
    a, b = design.net_of(f"{ref}.1"), design.net_of(f"{ref}.2")
    if a is None or b is None or a == b:
        return None
    ends = [_range(rails, a), _range(rails, b)]
    if ends[0] is None or ends[1] is None:
        return None
    (lo_a, hi_a, rail_a), (lo_b, hi_b, rail_b) = ends[0], ends[1]
    v = max(hi_a - lo_b, hi_b - lo_a)
    real = [r for r in (rail_a, rail_b) if r is not None]
    inferred = any(r.inferred or r.basis == "unknown" for r in real)
    basis = "; ".join(f"{r.net} {volts(r.min, r.max)}, {r.basis}" for r in real)
    return _Across(v, (a, b), inferred, basis)


def _range(rails: dict[str, PowerRail], net: str) -> tuple[float, float, PowerRail | None] | None:
    if is_ground(net):
        return 0.0, 0.0, None
    rail = rails.get(net)
    if rail is None or rail.min is None or rail.max is None:
        return None
    return rail.min, rail.max, rail


def _where(ref: str, value: str, across: _Across) -> str:
    a, b = across.nets
    return f"{ref} ({value}) across {a}–{b} ({across.basis})"


def _calc(
    ref: str, across: _Across, value: float, expected: float | None, flagged: bool, text: str
) -> Calculation:
    return Calculation(
        id="",
        kind="part_rating",
        ic=ref,
        pins=[f"{ref}.1", f"{ref}.2"],
        value=value,
        unit="V",
        expected=expected,
        flagged=flagged,
        text=text,
    )


def _led_currents(calculations: Sequence[Calculation]) -> dict[str, float]:
    """The highest current through each resistor in series with an LED,
    mA, from the ``led_current`` calculations."""
    found: dict[str, float] = {}
    for c in calculations:
        if c.kind != "led_current" or c.value is None:
            continue
        for pin in c.pins:
            ref = pin.split(".", 1)[0]
            if ref.rstrip("0123456789") == "R":
                found[ref] = max(found.get(ref, 0.0), c.value)
    return found


def _ref_order(ref: str) -> tuple[str, int]:
    """C2 before C10."""
    head = ref.rstrip("0123456789")
    tail = ref[len(head) :]
    return head, int(tail) if tail else 0
