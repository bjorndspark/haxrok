"""The order an IC's supplies come up in, against the order its datasheet
requires (``SequencingFact``, read by ``facts.py``).

Each side of a requirement is the rail its pins are on. Rails joined by a
pass part (ferrite, fuse, 0 Ω, diode) come up together, so they count as
the rail they're fed from. A rail is up no later than another when the
wiring fixes it:

- it feeds the part that makes the other (the regulator's input);
- it's on the other's enable: tied to it, pulled up to it, or the
  power-good pin of the regulator that makes it driving it;
- it supplies the IC driving the other's enable (that IC has to be on to
  drive it high);

and so on down the chain. A rail with two makers (a power mux) counts only
what both of them wait for.

- **first** (one supply up before or with the other): fine when the
  wiring brings the first up no later than the second, or both share a
  rail; flagged when the wiring brings the second up first; otherwise
  "may fail": two rails whose order rests on start-up times the netlist
  can't show.
- **together**: fine on one rail; otherwise "may fail" for the same reason.

A time limit is quoted, not computed. Results are ``Calculation``s (kind
``rail_order``), numbered after the others. Pure functions, no I/O.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.features.debug.calc import is_ground
from app.features.debug.design import Design, is_ic
from app.features.debug.schemas import Calculation, PowerPart, PowerTree, SequencingFact
from app.features.debug.states import enable_pins, resistors_on

# A power-good output, by its pin function: PG, PGOOD, POK, PWRGD, PWR_GOOD.
_POWER_GOOD = re.compile(r"P_?GOOD|PG\d*|POK|PWR_?GD|PWR_?GOOD|PWROK", re.IGNORECASE)

_OK, _MAY_FAIL, _FLAGGED = 0, 1, 2


@dataclass
class _Order:
    """What the wiring says about when each rail comes up, worked out once
    per board."""

    design: Design
    tree: PowerTree
    makers: dict[str, list[PowerPart]] = field(default_factory=dict)
    rails: set[str] = field(default_factory=set)
    _before: dict[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rails = {r.net for r in self.tree.rails}
        for p in self.tree.parts:
            # A charger doesn't bring its battery net up: the battery does.
            if p.kind != "charger":
                self.makers.setdefault(p.output, []).append(p)

    def root(self, rail: str, seen: frozenset[str] = frozenset()) -> str:
        """The rail ``rail`` comes up with: itself, or for a rail made only
        by pass parts from one rail, that rail's root."""
        makers = self.makers.get(rail, [])
        inputs = {i for p in makers for i in p.inputs}
        if (
            makers
            and all(p.kind == "pass" for p in makers)
            and len(inputs) == 1
            and rail not in seen
        ):
            return self.root(next(iter(inputs)), seen | {rail})
        return rail

    def before(self, rail: str, seen: frozenset[str] = frozenset()) -> frozenset[str]:
        """The roots the wiring brings up no later than ``rail``'s root."""
        rail = self.root(rail)
        if rail in self._before:
            return self._before[rail]
        if rail in seen:
            return frozenset()
        seen = seen | {rail}
        makers = self.makers.get(rail, [])
        got = (
            frozenset.intersection(*(self._waits_for(p, seen) for p in makers))
            if makers
            else frozenset()
        )
        got -= {rail}
        self._before[rail] = got
        return got

    def _waits_for(self, part: PowerPart, seen: frozenset[str]) -> frozenset[str]:
        """The roots ``part`` can't start before: its input (any one of
        them, so only what they all wait for) and its enable's sources."""
        per_input = [self._with_before(i, seen) for i in part.inputs]
        got = frozenset.intersection(*per_input) if per_input else frozenset()
        if is_ic(part.ref):
            for net in self.enable_sources(part.ref):
                got |= self._with_before(net, seen)
        return got

    def _with_before(self, rail: str, seen: frozenset[str]) -> frozenset[str]:
        return frozenset({self.root(rail)}) | self.before(rail, seen)

    def enable_sources(self, ref: str) -> list[str]:
        """The rails an IC's run-high enables wait for: the rail an enable is
        tied or pulled up to, the rail made by the regulator whose power-good
        pin drives it, or the supplies of any other IC driving it."""
        found: list[str] = []
        for e in enable_pins(self.design, ref):
            if not e.runs_high or is_ground(e.net):
                continue
            if e.net in self.rails:
                found.append(e.net)
                continue
            drivers = [n for n in self.design.nets.get(e.net, []) if n.ref != ref and is_ic(n.ref)]
            if drivers:
                for n in drivers:
                    fn = n.function.removesuffix(f"_{n.pin}")
                    made = [p.output for p in self.tree.parts if p.ref == n.ref]
                    if made and _POWER_GOOD.fullmatch(re.sub(r"[^A-Z0-9_]", "", fn.upper())):
                        found += made
                    else:
                        found += [r.net for r in self.tree.rails if n.ref in r.loads]
                continue
            ups = [o for _, o in resistors_on(self.design, e.net) if o in self.rails]
            if len(ups) == 1:
                found.append(ups[0])
        return found

    def how(self, rail: str) -> str:
        """How ``rail`` comes up, in words: its makers, their inputs and
        what their enables wait for."""
        root = self.root(rail)
        via = (
            f"{rail} (through {', '.join(self._pass_refs(rail))} from {root})"
            if root != rail
            else rail
        )
        makers = self.makers.get(root, [])
        if not makers:
            return f"{via} is a source"
        bits = []
        for p in makers:
            line = f"{p.ref} ({p.value}) from {', '.join(p.inputs)}"
            if is_ic(p.ref) and (on := self.enable_sources(p.ref)):
                line += f", enabled from {', '.join(dict.fromkeys(on))}"
            bits.append(line)
        return f"{via} comes from {'; '.join(bits)}"

    def _pass_refs(self, rail: str) -> list[str]:
        refs: list[str] = []
        while (makers := self.makers.get(rail)) and all(p.kind == "pass" for p in makers):
            refs += [p.ref for p in makers]
            nxt = makers[0].inputs[0] if makers[0].inputs else rail
            if nxt == rail or len(refs) > 10:
                break
            rail = nxt
        return refs


def check(
    design: Design,
    tree: PowerTree,
    sequencing: Mapping[str, Sequence[SequencingFact]],
    first_id: int = 1,
) -> list[Calculation]:
    """One calculation per datasheet sequencing requirement the wiring
    breaks or can't settle, flagged ones first, ids ``cf<first_id>``…; see
    the module docstring. ``sequencing`` is each IC's, by ref."""
    order = _Order(design, tree)
    out: list[Calculation] = []
    for ref in sorted(sequencing):
        for fact in sequencing[ref]:
            got = _one(order, ref, fact)
            if got is not None:
                out.append(got)
    out.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def _one(order: _Order, ref: str, fact: SequencingFact) -> Calculation | None:
    first = _rails(order, ref, fact.first)
    then = _rails(order, ref, fact.then)
    worst, pair = _OK, ("", "")
    for a in first:
        for b in then:
            v = _verdict(order, fact, a, b)
            if v > worst:
                worst, pair = v, (a, b)
    if worst == _OK:
        return None
    a, b = pair
    need = (
        f"{ref} needs pins {', '.join(fact.first)} ({a}) up before or with pins "
        f"{', '.join(fact.then)} ({b})"
        if fact.order == "first"
        else f"{ref} needs pins {', '.join(fact.first)} ({a}) and {', '.join(fact.then)} ({b}) "
        "up together"
    )
    limit = f", {fact.time_limit}" if fact.time_limit else ""
    text = f'{need}{limit} (p.{fact.page}: "{fact.text}"). {order.how(a)}; {order.how(b)}.'
    if worst == _FLAGGED:
        text += f" The wiring brings {b} up first, so {a} comes up after it."
    else:
        text += (
            " May fail: they come from different rails and the netlist can't show which "
            "starts first; check the start-up times."
        )
    return Calculation(
        id="",
        kind="rail_order",
        ic=ref,
        pins=[f"{ref}.{p}" for p in [*fact.first, *fact.then]],
        value=None,
        unit="",
        expected=None,
        flagged=worst == _FLAGGED,
        text=text,
    )


def _verdict(order: _Order, fact: SequencingFact, a: str, b: str) -> int:
    ra, rb = order.root(a), order.root(b)
    if ra == rb:
        return _OK
    if fact.order == "together":
        return _MAY_FAIL
    if ra in order.before(rb):
        return _OK
    if rb in order.before(ra):
        return _FLAGGED
    return _MAY_FAIL


def _rails(order: _Order, ref: str, pins: Sequence[str]) -> list[str]:
    """The power-tree rails ``ref``'s ``pins`` are on, in pin order."""
    nets = [order.design.net_of(f"{ref}.{p}") for p in pins]
    return list(dict.fromkeys(n for n in nets if n is not None and n in order.rails))
