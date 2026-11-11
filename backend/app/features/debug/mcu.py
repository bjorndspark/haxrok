"""A microcontroller's pins against its datasheet's pin rules.

The rules come from the datasheet (``PinRuleFact``, read with the facts), or
from the hand-written pin table (``pins.PartPins.rules``) where no datasheet
gave any. Each is checked on the netlist alone:

- reserved (in-package flash or PSRAM, "do not connect"): the pin's net
  reaches another part. Flagged, verified.
- input only: the pin is the only thing that could drive an LED or a
  transistor on its net (directly or through one resistor). Flagged.
- strapping: the pin's level at reset, from the netlist with every switch
  and jumper open and capacitors ignored: tied (its net is ground or a
  rail) or pulled (resistors to ground or to a rail, all one way). Tied to
  the level the normal boot doesn't want is flagged; pulled to it is
  flagged when nothing else is on the net, and "may fail" (listed) when
  something is, since that may drive it. A load (another IC, an LED, a
  connector, a resistor to anything else) with no defined level is "may
  fail". A pin the normal boot doesn't depend on (``either``) isn't
  checked.
- USB D+ / D-: the pin's net (directly or through one series resistor)
  reaches a pin named for the other line (a connector's D-), and none
  named for its own. Flagged: the lines are swapped.

A rule with a ``condition`` (a variant the part's value doesn't settle) is
never flagged, only listed with its condition. Results are
``Calculation``s (kind ``mcu_pin``), one per pin and rule, flagged first.
Pure functions, no I/O.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from app.features.debug.calc import is_ground, named_voltage
from app.features.debug.design import Design, Node, is_ic
from app.features.debug.pins import PIN_TABLE, pin_order
from app.features.debug.schemas import (
    BootLevel,
    Calculation,
    PinRuleFact,
    PinRuleKind,
    PowerTree,
)


@dataclass(frozen=True)
class Rule:
    """One pin rule of one IC, from its datasheet or the pin table."""

    kind: PinRuleKind
    pins: tuple[str, ...]
    text: str
    source: str  # "datasheet p.12", or the pin table's document
    boot_level: BootLevel = "either"
    condition: str = ""


def rules(
    design: Design, datasheet: Mapping[str, Sequence[PinRuleFact]] | None = None
) -> dict[str, list[Rule]]:
    """Every IC's pin rules: its datasheet's (``datasheet``, ref -> rules)
    where that gave any, else the pin table's."""
    out: dict[str, list[Rule]] = {}
    for ref, part in design.parts.items():
        if not is_ic(ref):
            continue
        if read := (datasheet or {}).get(ref):
            out[ref] = [_rule(f, f"datasheet p.{f.page}") for f in read]
            continue
        entry = next(
            (e for e in PIN_TABLE if e.matches(part.value, part.lib_part, part.footprint)), None
        )
        if entry is not None and entry.rules:
            out[ref] = [_rule(f, entry.source) for f in entry.rules]
    return out


def _rule(fact: PinRuleFact, source: str) -> Rule:
    return Rule(
        kind=fact.kind,
        pins=tuple(fact.pins),
        text=fact.text,
        source=source,
        boot_level=fact.boot_level,
        condition=fact.condition,
    )


@dataclass
class _Level:
    """What sets a pin's level at reset: a tie, pulls, and anything else
    on its net that may drive it."""

    tied: BootLevel | None = None
    pulls: dict[str, BootLevel] = field(default_factory=dict)  # resistor ref -> way
    loads: list[str] = field(default_factory=list)

    @property
    def level(self) -> BootLevel | None:
        """The level the netlist sets: the tie, else the pulls if they all
        pull one way; None when nothing (or a divider) sets it."""
        if self.tied:
            return self.tied
        ways = set(self.pulls.values())
        return ways.pop() if len(ways) == 1 else None


def check(
    design: Design, tree: PowerTree, rules: Mapping[str, Sequence[Rule]], first_id: int = 1
) -> list[Calculation]:
    """One calculation per IC pin used against its rule, flagged ones
    first, ids ``cf<first_id>``…; see the module docstring."""
    rails = {r.net for r in tree.rails if not is_ground(r.net)}
    out: list[Calculation] = []
    for ref in sorted(rules):
        if ref not in design.parts:
            continue
        for rule in rules[ref]:
            for pin in sorted(rule.pins, key=pin_order):
                pin_ref = f"{ref}.{pin}"
                net = design.net_of(pin_ref)
                if net is None:
                    continue
                found = _check_pin(design, rails, rule, pin_ref, net)
                if found is not None:
                    out.append(found)
    out.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(out, start=first_id)]


def _check_pin(
    design: Design, rails: Collection[str], rule: Rule, pin_ref: str, net: str
) -> Calculation | None:
    match rule.kind:
        case "reserved":
            others = sorted({n.ref for n in design.nets[net] if n.pin_ref != pin_ref})
            if not others and not _is_rail(net, rails):
                return None
            on = ", ".join(others) if others else net
            return _calc(
                design,
                rule,
                pin_ref,
                [],
                f"is reserved, but its net {net} reaches {on}.",
                "Leave it unconnected.",
            )
        case "input_only":
            driven = _driven_loads(design, net, pin_ref)
            if not driven:
                return None
            return _calc(
                design,
                rule,
                pin_ref,
                [],
                f"is input only, but it's the only pin that could drive "
                f"{', '.join(driven)} on {net}.",
                "Move the output to a pin that can drive.",
            )
        case "strapping":
            if rule.boot_level == "either":
                return None
            return _strapping(design, rails, rule, pin_ref, net)
        case "usb_d_plus" | "usb_d_minus":
            own, other = (_PLUS, _MINUS) if rule.kind == "usb_d_plus" else (_MINUS, _PLUS)
            ends = _usb_ends(design, net, pin_ref)
            wrong = [n.pin_ref for n in ends if other.fullmatch(n.function)]
            if not wrong or any(own.fullmatch(n.function) for n in ends):
                return None
            line = "D+" if rule.kind == "usb_d_plus" else "D-"
            names = ", ".join(f"{p} ({design.pin_name(p)})" for p in wrong)
            return _calc(
                design,
                rule,
                pin_ref,
                wrong,
                f"is the built-in USB's {line}, but its net {net} reaches {names}: "
                "D+ and D- are swapped.",
                f"Wire it to the connector's {line}.",
            )
    return None


def _strapping(
    design: Design, rails: Collection[str], rule: Rule, pin_ref: str, net: str
) -> Calculation | None:
    want = rule.boot_level
    at = _level(design, rails, net, pin_ref)
    level = at.level
    pulls = sorted(at.pulls)
    if at.tied and at.tied != want:
        what = f"is tied {at.tied} ({net})"
        flagged = True
    elif level and level != want:
        what = f"is pulled {level} by {', '.join(pulls)} ({net})"
        flagged = not at.loads
        what += f"; {', '.join(at.loads)} also on its net may override that" if at.loads else ""
    elif level is None and at.loads:
        how = f" ({', '.join(pulls)} pull both ways)" if pulls else ""
        what = f"has no defined level at reset{how}, and {', '.join(at.loads)} on {net} may set it"
        flagged = False
    else:
        return None
    resistors = [f"{r}.{p}" for r in pulls for p in ("1", "2")]
    fix = f"It must be {want} at reset for the normal boot from flash; " + (
        f"pull it {'up' if want == 'high' else 'down'} with a resistor"
        if flagged
        else "check what drives it during reset"
    )
    return _calc(
        design,
        rule,
        pin_ref,
        resistors,
        f"is a strapping pin and {what}.",
        fix + ".",
        may_fail=not flagged,
    )


def _calc(
    design: Design,
    rule: Rule,
    pin_ref: str,
    others: list[str],
    what: str,
    fix: str,
    may_fail: bool = False,
) -> Calculation:
    ref = pin_ref.split(".")[0]
    name = design.pin_name(pin_ref)
    named = f"{pin_ref} ({name})" if name else pin_ref
    flagged = not may_fail and not rule.condition
    text = (
        f"{ref} ({design.parts[ref].value}): {named} {what} {fix} "
        f'Rule ({rule.source}): "{rule.text}"'
    )
    if rule.condition:
        text += f" Not flagged: it applies {rule.condition}; check that it applies to this part."
    elif may_fail:
        text += " May fail, not verified: the netlist doesn't show the level during reset."
    return Calculation(
        id="",
        kind="mcu_pin",
        ic=ref,
        pins=[pin_ref, *others],
        value=None,
        unit="",
        expected=None,
        flagged=flagged,
        text=text,
    )


# --- netlist reading -----------------------------------------------------------------

# USB data line names on other parts' pins: a connector's D+ / DP1 / D_P,
# a bridge's UD+ or USB_DP; the minus side alike.
_PLUS = re.compile(r"(U|USB[_-]?)?D(\+|P|_P)\d?", re.IGNORECASE)
_MINUS = re.compile(r"(U|USB[_-]?)?D(-|−|M|N|_M|_N)\d?", re.IGNORECASE)

# Parts that don't drive a pin at reset: a switch or jumper is taken as open,
# a capacitor sets no DC level, a test point is a pad.
_QUIET = re.compile(r"^(SW|JP|C|TP)\d", re.IGNORECASE)
_SWITCH_LIB = re.compile(r"SW|SPDT|SPST|JUMPER", re.IGNORECASE)


def _head(ref: str) -> str:
    return ref.rstrip("0123456789")


def _is_rail(net: str, rails: Collection[str]) -> bool:
    return net in rails or (named_voltage(net) or 0) > 0


def _quiet(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    return _QUIET.match(ref) is not None or (
        part is not None and _SWITCH_LIB.search(part.lib_part) is not None
    )


def _other_end(design: Design, ref: str, net: str) -> str | None:
    """A two-pin part's net at its other end."""
    nets = [
        other
        for other, nodes in design.nets.items()
        if other != net and any(n.ref == ref for n in nodes)
    ]
    return nets[0] if len(nets) == 1 else None


def _level(design: Design, rails: Collection[str], net: str, pin_ref: str) -> _Level:
    """What sets ``net``'s level at reset, seen from ``pin_ref``."""
    at = _Level()
    if is_ground(net):
        at.tied = "low"
        return at
    if _is_rail(net, rails):
        at.tied = "high"
        return at
    for node in design.nets[net]:
        if node.pin_ref == pin_ref or _quiet(design, node.ref) or node.ref in at.pulls:
            continue
        if _head(node.ref) == "R" and (far := _other_end(design, node.ref, net)):
            if is_ground(far):
                at.pulls[node.ref] = "low"
                continue
            if _is_rail(far, rails):
                at.pulls[node.ref] = "high"
                continue
            # A resistor to a button or a pad only is still open at reset.
            if all(n.ref == node.ref or _quiet(design, n.ref) for n in design.nets[far]):
                continue
        if node.ref not in at.loads:
            at.loads.append(node.ref)
    return at


def _driven_loads(design: Design, net: str, pin_ref: str) -> list[str]:
    """LEDs and transistors on ``net`` (or one resistor on) that only
    ``pin_ref`` could drive; [] when another IC or a connector is there."""
    nets = [net]
    for node in design.nets[net]:
        far = _other_end(design, node.ref, net) if _head(node.ref) == "R" else None
        if far and not is_ground(far) and (named_voltage(far) or 0) <= 0:
            nets.append(far)
    nodes = [n for on in nets for n in design.nets[on] if n.pin_ref != pin_ref]
    if any(is_ic(n.ref) or _head(n.ref) in {"J", "P", "CN"} for n in nodes):
        return []
    return sorted({n.ref for n in nodes if _head(n.ref) == "Q" or _is_led(design, n.ref)})


def _is_led(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    return (
        part is not None
        and _head(ref) in {"D", "LED"}
        and part.lib_part.rsplit(":", 1)[-1].upper().startswith("LED")
    )


def _usb_ends(design: Design, net: str, pin_ref: str) -> list[Node]:
    """Other parts' pins on ``net``, and one series resistor on."""
    ends = [n for n in design.nets[net] if n.pin_ref != pin_ref]
    for node in list(ends):
        if _head(node.ref) == "R" and (far := _other_end(design, node.ref, net)):
            ends += [n for n in design.nets[far] if n.ref != node.ref]
    return [n for n in ends if _head(n.ref) not in {"R", "C"}]
