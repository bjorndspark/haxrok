"""Each rail's current budget: the load its parts draw against what the
part making it is rated for.

Once the datasheets are read, every part on a rail draws:

- an IC with a supply pin on it: its datasheet's maximum supply current
  (the highest active mode, the max column, else the typical). A figure
  naming supply pins counts on the rail those pins are on; one naming
  none counts on every rail the IC draws from, since the netlist can't
  split it;
- a regulator, power mux or pass part taking power from it: its output
  rail's load, scaled by output over input voltage when it steps up
  (lossless, so it's a floor), plus its own supply current when given; a
  mux counts its whole load on each input, since either may carry it;
- a charger: its charge current (``calc``'s ``charge_current``).

A part with no figure is unknown, and so is every rail above it: the rail
reads "at least X mA, N parts unknown". The rating is the rated output
current of the regulator or switch making the rail (``spice_circuits.
ratings``); a rail made by pass parts, a battery or USB has none. A known
load above the rating is "may fail", never verified, since peaks rarely
coincide.

Pure functions, no I/O.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.features.debug.design import Design
from app.features.debug.schemas import (
    Calculation,
    LimitFact,
    PowerPart,
    PowerRail,
    PowerTree,
    RailBudget,
    RailLoad,
)
from app.features.debug.spice_circuits import ratings, scaled

# "supply current" is the name the extraction prompt asks for; the rest are
# what datasheets print when the model keeps their wording.
_SUPPLY_CURRENT = re.compile(
    r"supply\s+current|current\s+consumption|operating\s+current|quiescent\s+current"
    r"|^I_?(DD|CC|Q)[A-Z]{0,2}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Draw:
    ma: float | None
    note: str


def apply(
    design: Design,
    tree: PowerTree,
    limits: Mapping[str, Sequence[LimitFact]],
    calculations: Sequence[Calculation] = (),
) -> PowerTree:
    """``tree`` with every rail's ``budget`` filled; see the module docstring."""
    rails = {r.net: r for r in tree.rails}
    taking: dict[tuple[str, str], list[PowerPart]] = {}
    for p in tree.parts:
        for net in p.inputs:
            taking.setdefault((p.ref, net), []).append(p)
    # ref -> its pins on rails it draws from as an IC, for attributing figures
    ic_pins: dict[str, set[str]] = {}
    ic_rails: dict[str, int] = {}
    for r in tree.rails:
        for ref in r.loads:
            if (ref, r.net) not in taking:
                ic_pins.setdefault(ref, set()).update(_pins_on(design, ref, r.net))
                ic_rails[ref] = ic_rails.get(ref, 0) + 1
    charge = {c.ic: c for c in calculations if c.kind == "charge_current" and c.value}
    done: dict[str, RailBudget] = {}

    def budget(net: str, seen: frozenset[str]) -> RailBudget:
        if net in done:
            return done[net]
        rail = rails[net]
        loads: list[RailLoad] = []
        unknown: list[str] = []
        for ref in rail.loads:
            parts = taking.get((ref, net))
            if not parts:
                draw = _supply(
                    limits.get(ref, ()),
                    _pins_on(design, ref, net),
                    ic_pins.get(ref, set()),
                    ic_rails.get(ref, 1),
                )
                if draw.ma is None:
                    unknown.append(ref)
                loads.append(RailLoad(ref=ref, ma=draw.ma, note=draw.note))
                continue
            total: float | None = 0.0
            notes: list[str] = []
            for p in parts:
                if p.kind == "charger":
                    c = charge.get(ref)
                    if c is None or c.value is None:
                        total = None
                        notes.append("charge current unknown")
                    elif total is not None:
                        total += c.value
                        notes.append(f"charge current ({c.id})")
                    continue
                if p.output in seen or p.output == net or p.output not in rails:
                    continue
                down = budget(p.output, seen | {net})
                factor = _step_up(rails[p.output], rail) if p.kind == "regulator" else 1.0
                if total is not None:
                    total += down.load_ma * factor
                unknown += [u for u in down.unknown if u not in unknown]
                scale = f" ×{factor:.2f}, stepped up" if factor > 1 else ""
                notes.append(f"{p.output}'s load{scale}")
            own = _supply(limits.get(ref, ()), set(), set(), 1)
            if own.ma is not None and total is not None:
                total += own.ma
                notes.append(f"own {own.note}")
            if total is None and ref not in unknown:
                unknown.append(ref)
            ma = round(total, 3) if total is not None else None
            loads.append(RailLoad(ref=ref, ma=ma, note=", ".join(notes)))
        load = round(sum(x.ma for x in loads if x.ma is not None), 3)
        rated, rated_note = _rating(net, tree.parts, limits)
        out = RailBudget(
            load_ma=load,
            unknown=unknown,
            loads=loads,
            rated_ma=rated,
            rated_note=rated_note,
            over=rated is not None and load > rated,
        )
        done[net] = out
        return out

    return tree.model_copy(
        update={
            "rails": [
                r.model_copy(update={"budget": budget(r.net, frozenset())}) for r in tree.rails
            ]
        }
    )


def _supply(limits: Sequence[LimitFact], here: set[str], on_rails: set[str], n_rails: int) -> _Draw:
    """The most an IC draws through the pins ``here``: its figures naming
    those pins, else those naming no rail pin (``on_rails`` are all its
    pins on rails), the max column, else the typical."""
    lines = [
        lim
        for lim in limits
        if lim.kind != "absolute_maximum" and _SUPPLY_CURRENT.search(lim.parameter)
    ]
    mine = [lim for lim in lines if here & set(lim.pins)]
    whole = not mine
    if whole:
        mine = [lim for lim in lines if not (on_rails & set(lim.pins))]
    best: tuple[float, LimitFact, bool] | None = None
    for lim in mine:
        for value, typical in ((lim.max, False), (lim.typ, True)):
            amps = scaled(value, lim.unit, "A")
            if amps is not None and amps > 0:
                if best is None or amps * 1000 > best[0]:
                    best = (amps * 1000, lim, typical)
                break
    if best is None:
        return _Draw(None, "no supply-current figure")
    ma, lim, typical = best
    note = f"datasheet p.{lim.page}" + (", typical" if typical else "")
    if whole and n_rails > 1:
        note += ", whole part, counted on each of its rails"
    return _Draw(round(ma, 3), note)


def _step_up(out: PowerRail, into: PowerRail) -> float:
    """Output over input voltage when a regulator steps up (input current
    above its output's, lossless), else 1; 1 when either is unknown."""
    if out.max is None or into.min is None or into.min <= 0:
        return 1.0
    return max(1.0, out.max / into.min)


def _rating(
    net: str, parts: Sequence[PowerPart], limits: Mapping[str, Sequence[LimitFact]]
) -> tuple[float | None, str]:
    """The rated output current of the regulators and switches making
    ``net``, the largest when several do (one carries it at a time); none
    when any maker has no rating or is a pass part or charger."""
    makers = [p for p in parts if p.output == net]
    if not makers or any(p.kind not in ("regulator", "switch") for p in makers):
        return None, ""
    best: tuple[float, str] | None = None
    for p in makers:
        r = ratings(limits.get(p.ref, ()))
        if r.current is None:
            return None, ""
        ma = r.current * 1000
        if best is None or ma > best[0]:
            best = (ma, f"{p.ref} datasheet p.{r.current_page}")
    return (round(best[0], 3), best[1]) if best else (None, "")


def _pins_on(design: Design, ref: str, net: str) -> set[str]:
    return {n.pin for n in design.nets.get(net, []) if n.ref == ref}
