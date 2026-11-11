"""Each IC pin on a rail against its datasheet's voltage limits.

Once the datasheets are read, every limit says which board pins it applies
to (``LimitFact.pins``, or ``all_pins`` for "voltage on any pin"). For each
IC pin on a power-tree rail with a known range, the pin's own limits are
compared with that range, and the "any pin" ones only where the pin has
none of its own:

- above the absolute maximum (or below its minimum): flagged, verified,
  when the rail's voltage isn't inferred (named, calculated, a fixed
  regulator's value, its datasheet, USB, the user's battery value). On an
  inferred voltage (a battery's Li-ion default) it's listed, not flagged;
- outside the recommended operating range but within the absolute
  maximum: listed, never flagged.

Several limits of one table for the same pin (a transient and a steady
rating) count as the widest range, so a pin is never flagged against a
rating that another line of the same table relaxes. A limit given relative
to another pin ("VDD + 0.3 V") has no number and isn't checked.

Results are ``Calculation``s (kind ``pin_voltage``), numbered after the
netlist's own. Pure functions, no I/O.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.features.debug.calc import is_ground
from app.features.debug.design import Design
from app.features.debug.pins import pin_order
from app.features.debug.power import to_volts, volts
from app.features.debug.schemas import Calculation, LimitFact, LimitKind, PowerRail, PowerTree

# Rail and limit values are rounded to mV: a 3.3 V rail on a 3.3 V maximum
# is within it.
_EPS = 1e-3

Side = Literal["over", "under"]


@dataclass(frozen=True)
class _Bound:
    """The widest range one table gives a pin, and the limit lines it came
    from (the one setting each side, for the citation)."""

    lo: float | None
    hi: float | None
    lo_from: LimitFact | None
    hi_from: LimitFact | None


@dataclass(frozen=True)
class _Breach:
    kind: LimitKind
    side: Side
    limit: LimitFact
    bound: float


def check(
    design: Design, tree: PowerTree, limits: Mapping[str, Sequence[LimitFact]], first_id: int = 1
) -> list[Calculation]:
    """One calculation per IC, rail and limit crossed, flagged ones first,
    ids ``cf<first_id>``…; see the module docstring."""
    rails = {r.net: r for r in tree.rails if not is_ground(r.net)}
    groups: dict[tuple[str, str, LimitKind, Side, int], tuple[_Breach, list[str]]] = {}
    for ref in sorted(limits):
        ic_limits = [lim for lim in limits[ref] if lim.kind != "characteristic"]
        if not ic_limits:
            continue
        for net, rail in rails.items():
            if rail.min is None or rail.max is None:
                continue
            on = sorted((n.pin for n in design.nets.get(net, []) if n.ref == ref), key=pin_order)
            for pin in on:
                breach = _breach(rail.min, rail.max, pin, ic_limits)
                if breach is None:
                    continue
                key = (ref, net, breach.kind, breach.side, id(breach.limit))
                groups.setdefault(key, (breach, []))[1].append(pin)
    out = [
        _calculation(design, ref, rails[net], breach, pins)
        for (ref, net, *_), (breach, pins) in groups.items()
    ]
    out.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def _breach(lo: float, hi: float, pin: str, limits: Sequence[LimitFact]) -> _Breach | None:
    """The worst limit a rail of ``lo``-``hi`` V crosses on ``pin``: the
    absolute maximum first, else the recommended range; None when within
    both."""
    for kind in ("absolute_maximum", "recommended"):
        bound = _bound(pin, [lim for lim in limits if lim.kind == kind])
        if bound is None:
            continue
        if bound.hi is not None and bound.hi_from and hi > bound.hi + _EPS:
            return _Breach(kind, "over", bound.hi_from, bound.hi)
        if bound.lo is not None and bound.lo_from and lo < bound.lo - _EPS:
            return _Breach(kind, "under", bound.lo_from, bound.lo)
    return None


def _bound(pin: str, limits: Sequence[LimitFact]) -> _Bound | None:
    """The widest voltage range ``limits`` give ``pin``: its own lines, or
    the every-pin lines when it has none. None when neither gives a number."""
    own = [lim for lim in limits if pin in lim.pins]
    lines = own or [lim for lim in limits if lim.all_pins]
    lo = hi = None
    lo_from = hi_from = None
    for lim in lines:
        mn, mx = to_volts(lim.min, lim.unit), to_volts(lim.max, lim.unit)
        if mn is not None and (lo is None or mn < lo):
            lo, lo_from = mn, lim
        if mx is not None and (hi is None or mx > hi):
            hi, hi_from = mx, lim
    if lo is None and hi is None:
        return None
    return _Bound(lo, hi, lo_from, hi_from)


def _calculation(
    design: Design, ref: str, rail: PowerRail, breach: _Breach, pins: list[str]
) -> Calculation:
    pin_refs = [f"{ref}.{p}" for p in pins]
    named = ", ".join(f"{r} ({name})" if (name := design.pin_name(r)) else r for r in pin_refs)
    value = (rail.max if breach.side == "over" else rail.min) or 0.0
    absolute = breach.kind == "absolute_maximum"
    known = not rail.inferred and rail.basis != "unknown"
    flagged = absolute and known
    lim = breach.limit
    word = "above" if breach.side == "over" else "below"
    edge = "maximum" if breach.side == "over" else "minimum"
    rating = f"absolute {edge}" if absolute else f"recommended operating {edge}"
    basis = rail.basis + (f", {rail.note}" if rail.note else "")
    text = (
        f"{named} on {rail.net} ({volts(rail.min, rail.max)}; {basis}): "
        f"{value:g} V is {word} {ref}'s {rating} {breach.bound:g} V "
        f'("{lim.parameter}", datasheet p.{lim.page}).'
    )
    if flagged:
        text += " The part can be damaged with the rail at that voltage."
    elif absolute:
        text += (
            " Not flagged: the rail's voltage is inferred; check it (a battery's range can be "
            "set on the review form)."
        )
    else:
        text += (
            " Not flagged: outside the recommended range only, where the datasheet doesn't "
            "promise the part works as specified."
        )
    return Calculation(
        id="",
        kind="pin_voltage",
        ic=ref,
        pins=pin_refs,
        value=value,
        unit="V",
        expected=breach.bound if flagged else None,
        flagged=flagged,
        text=text,
    )
