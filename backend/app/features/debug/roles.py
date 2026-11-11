"""Pin roles, and the verified wiring rules built on them.

Every IC pin gets a role (``PinRole``): from its datasheet when one was read
(``facts.FactReader``), else from the hand-written pin table (its
``inductor_pins`` are switch nodes), else from the pin's name ("L2", "FB").
Three rules then hold the board to what the roles keep apart:

- a switch node connects only to its inductor and the small parts around it
  (bootstrap capacitor, snubber, catch diode): never to a rail, another IC,
  or another of its own IC's pins;
- a feedback input senses its IC's own output: never ground or another rail;
- a required part of the datasheet's typical application never has two of
  its IC pins on one net, which shorts the part out.

Each rule reads only the netlist and the roles, so a finding is verified,
not the model's reasoning. They exist because a private board's buck-boost
had its L2 pin on its own output: the model saw it, but ranked "a switch
node is its own net" as recalled topology, and only a hand-written table
entry made it a finding.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from app.features.debug.calc import is_ground, named_voltage
from app.features.debug.design import Design, Label, Node, is_ic, is_inductor, short_net_name
from app.features.debug.schemas import AppPart, ErcEntry, PinRole, WiringFinding, WiringFix

# Where a role came from, as a finding names it. A name-only role is the
# weakest: it only counts on an IC that looks like a regulator (``assign``).
BY_NAME = "its pin name"
BY_DATASHEET = "its datasheet"

# Pin names, normalized to upper-case letters and digits, by role. Checked
# in order; a name matching none gets no role from its name.
_NAME_ROLES: tuple[tuple[PinRole, re.Pattern[str]], ...] = (
    ("switch", re.compile(r"(SW|LX)\d*|L[12]|PH")),
    ("feedback", re.compile(r"V?FB")),
    ("ground", re.compile(r"(A|D|P|S)?GND\d*|VSS\d*|EP|E?PAD")),
    ("supply_out", re.compile(r"V?OUT\d*|VOS|VO")),
    ("supply_in", re.compile(r"P?V?IN\d*|VI|VDD\w*|VCC\w*")),
    ("enable", re.compile(r"EN|CE|ENABLE")),
)
# Supply names strong enough to call the net a rail on their own; bare
# IN and OUT also name op-amp and logic pins.
_RAIL_PIN = re.compile(r"V(IN|OUT|DD|CC|I|O)\w*|PVIN\d*|VOS")
_SUPPLY_ROLES = frozenset({"supply_in", "supply_out"})
# Roles whose pins a datasheet keeps on separate nets. Enable, signal and
# other pins are often tied to a supply or ground (EN to VIN, MODE to GND),
# so a part listed on one of them can't show a short.
_POWER_ROLES = _SUPPLY_ROLES | {"ground", "switch", "feedback"}
# Kinds a short across two IC pins disables. Not resistors: a datasheet's
# pull-up is often replaced by a direct tie on a board.
_SHORTED_KINDS = frozenset({"capacitor", "inductor", "diode", "crystal"})
# Two-pin parts a regulator's output runs through to the rail it feeds
# (a 0R link, a filter inductor, a ferrite bead, an OR-ing diode).
_OUTPUT_HOP = frozenset({"R", "L", "FB", "D"})
# How many other pins a finding names before it says "and N more": a
# shorted power rail can carry 40-odd pins.
_MAX_LISTED = 6


@dataclass(frozen=True)
class Pin:
    """One IC pin as the rules read it."""

    number: str
    name: str
    role: PinRole
    function: str
    source: str  # where the role came from: BY_DATASHEET, a table's source, BY_NAME


IcPins = Mapping[str, Mapping[str, Pin]]
"""ref -> pin number -> its Pin, for every IC on the board."""


def name_role(name: str, number: str = "") -> PinRole | None:
    """The role a pin's name gives it, or None. KiCad 10 suffixes pin
    functions with the pin number ("L2_7"); that is dropped first."""
    key = _norm(name, number)
    return next((role for role, pattern in _NAME_ROLES if pattern.fullmatch(key)), None)


def assign(
    design: Design,
    ref: str,
    *,
    datasheet: Mapping[str, tuple[str, str]] | None = None,
    datasheet_roles: Mapping[str, PinRole] | None = None,
    table: Mapping[str, tuple[str, str]] | None = None,
    table_switch: Collection[str] = (),
    table_source: str = "",
) -> dict[str, Pin]:
    """Every connected pin of IC ``ref`` with its role. ``datasheet`` and
    ``table`` map pin number to (name, function); a datasheet role wins,
    then the table's switch pins, then the pin's name.

    A role from the name alone makes a switch node only on an IC that also
    has a feedback or output-sense pin: "SW" and "L1" name other things on
    other parts (a button input, an analog switch)."""
    board = {
        n.pin: n.function.removesuffix(f"_{n.pin}")  # KiCad 10's "L2_7"
        for nodes in design.nets.values()
        for n in nodes
        if n.ref == ref
    }
    pins: dict[str, Pin] = {}
    for number, drawn in board.items():
        name, function = (datasheet or {}).get(number) or (table or {}).get(number) or (drawn, "")
        name = name or drawn
        if datasheet_roles and number in datasheet_roles:
            pins[number] = Pin(number, name, datasheet_roles[number], function, BY_DATASHEET)
        elif number in table_switch:
            pins[number] = Pin(number, name, "switch", function, table_source)
        else:
            role = name_role(name, number) or name_role(drawn, number) or "other"
            pins[number] = Pin(number, name, role, function or drawn, BY_NAME)
    regulator = any(p.role == "feedback" or _norm(p.name, p.number) == "VOS" for p in pins.values())
    if not regulator:
        pins = {
            number: Pin(number, p.name, "other", p.function, p.source)
            if p.source == BY_NAME and p.role == "switch"
            else p
            for number, p in pins.items()
        }
    return pins


def check(
    design: Design,
    ics: IcPins,
    erc: Sequence[ErcEntry] = (),
    labels: Sequence[Label] = (),
    application: Mapping[str, Sequence[AppPart]] | None = None,
) -> list[WiringFinding]:
    """The three rules over every IC in ``ics``, in ref order. ``erc`` and
    ``labels`` let a finding name the label behind a short;
    ``application`` (ref -> its datasheet's typical-application parts)
    feeds the third rule. Ids are left for the caller to number."""
    rails = _rail_nets(design, ics)
    findings: list[WiringFinding] = []
    for ref in sorted(ics, key=_ref_key):
        pins = ics[ref]
        outputs = _own_outputs(design, ref, pins)
        numbers = sorted(pins, key=_pin_key)
        mine = [f for n in numbers if (f := _switch(design, ref, pins, n, rails))]
        mine += [f for n in numbers if (f := _feedback(design, ref, pins[n], outputs, rails))]
        flagged = {f.pin for f in mine}
        for part in (application or {}).get(ref, []):
            finding = _shorted(design, ref, pins, part, outputs)
            if finding is not None and not flagged & {finding.pin, *finding.conflicts}:
                mine.append(finding)
                flagged |= {finding.pin, *finding.conflicts}
        for f in mine:
            causes = label_causes(design, ref, f.pin_name, f.net, erc, labels)
            findings.append(f.model_copy(update={"text": " ".join([f.text, *causes])}))
    return findings


def _switch(
    design: Design, ref: str, pins: Mapping[str, Pin], number: str, rails: Mapping[str, set[str]]
) -> WiringFinding | None:
    """A switch pin whose net is a rail, reaches another IC or another of
    its IC's pins, or (by datasheet or table) has no inductor."""
    pin = pins[number]
    pin_ref = f"{ref}.{number}"
    net = design.net_of(pin_ref)
    if pin.role != "switch" or net is None:
        return None  # unconnected: KiCad's ERC reports that
    others = [n for n in design.nets[net] if n.pin_ref != pin_ref]
    inductors = [n for n in others if is_inductor(design, n.ref)]
    joined = [n for n in others if _joins(n, ref, pin, pins)]
    rail = _is_rail(net, rails, pin_ref)
    no_inductor = not inductors and pin.source != BY_NAME
    if not (joined or rail or no_inductor):
        return None
    # The IC's own pins first, then other ICs': L2 on VOUT is the telling
    # part, not the decoupling capacitors.
    foreign = [n for n in others if not is_inductor(design, n.ref) and _ref_head(n.ref) != "TP"]
    foreign.sort(key=lambda n: (n.ref != ref, not is_ic(n.ref), _ref_key(n.ref), _pin_key(n.pin)))
    if foreign:
        problem = f"but its net {net} also connects {_listed(foreign)}"
        if rail and not joined:
            problem += ", a supply or ground rail"
    elif rail:
        problem = f"but its net {net} is a supply or ground rail"
    else:
        problem = f"but its net {net} has no inductor on it"
    return WiringFinding(
        id="",
        pin=pin_ref,
        pin_name=pin.name,
        net=net,
        conflicts=[n.pin_ref for n in foreign],
        inductors=[n.pin_ref for n in inductors],
        nets=[net],
        text=(
            f"{pin_ref} ({pin.name}) is an inductor connection, a switch node "
            f"({_cited(pin)}), {problem}. It should connect only to its inductor."
        ),
        fix=_switch_fix(design, ref, pins, pin, net, inductors),
    )


def _switch_fix(
    design: Design,
    ref: str,
    pins: Mapping[str, Pin],
    pin: Pin,
    net: str,
    inductors: Sequence[Node],
) -> WiringFix | None:
    """The fix for a switch pin that shares its net with one inductor end
    whose other end is on another of its IC's switch pins: a buck-boost's
    inductor runs from L1 to this net, so the pin and that end belong on a
    net of their own (plan 006 step 3; the private board's U5.9, orbit's
    U15.7). Any other shape has no unambiguous fix, so None."""
    if len(inductors) != 1:
        return None
    end = inductors[0]
    far = _far_ends(design, end) - {net}
    if len(far) != 1:
        return None
    partner = next(
        (
            n
            for n in design.nets[far.pop()]
            if n.ref == ref
            and n.pin != pin.number
            and n.pin in pins
            and pins[n.pin].role == "switch"
        ),
        None,
    )
    if partner is None:
        return None
    pin_ref = f"{ref}.{pin.number}"
    joins = _free_name(design, f"Net-({ref}-{pin.name})")
    return WiringFix(
        pins=[pin_ref, end.pin_ref],
        leaves=net,
        joins=joins,
        new_net=True,
        text=(
            f"{pin_ref} ({pin.name}) and {end.pin_ref} leave {net} for a net of their own, "
            f"{joins}, so {end.ref} runs from {partner.pin_ref} ({pins[partner.pin].name}) "
            f"to {pin_ref} ({pin.name})."
        ),
    )


def _free_name(design: Design, name: str) -> str:
    """``name``, or ``name_2``, … when the design already has it."""
    got, n = name, 1
    while got in design.nets:
        n += 1
        got = f"{name}_{n}"
    return got


def apply_fix(design: Design, fix: WiringFix) -> Design:
    """``design`` with ``fix``'s pins moved from its ``leaves`` net to its
    ``joins`` net (created when new). The parts stay; ``design`` is untouched."""
    moved = set(fix.pins)
    nets = {
        name: [n for n in nodes if name != fix.leaves or n.pin_ref not in moved]
        for name, nodes in design.nets.items()
    }
    nets.setdefault(fix.joins, []).extend(
        n for n in design.nets.get(fix.leaves, []) if n.pin_ref in moved
    )
    return Design(dict(design.parts), nets)


def _feedback(
    design: Design, ref: str, pin: Pin, outputs: set[str], rails: Mapping[str, set[str]]
) -> WiringFinding | None:
    """A feedback pin straight on ground, or on a rail that isn't its IC's
    own output. Its own output is fine: fixed-output versions tie FB to
    VOUT. Ground counts only for a role from a datasheet or table whose
    function text doesn't say to ground it: some fixed versions do."""
    pin_ref = f"{ref}.{pin.number}"
    net = design.net_of(pin_ref)
    if pin.role != "feedback" or net is None or net in outputs:
        return None
    if is_ground(net):
        if pin.source == BY_NAME or re.search(r"GND|ground", pin.function, re.IGNORECASE):
            return None
        problem = f"its net {net} is ground"
    elif outputs and _is_rail(net, rails, pin_ref):
        own = ", ".join(sorted(outputs))
        problem = f"its net {net} is a supply rail, not {ref}'s output ({own})"
    else:
        return None  # its output unknown, or a divider node: not a rule's business
    others = [n for n in design.nets[net] if n.pin_ref != pin_ref]
    others.sort(key=lambda n: (not is_ic(n.ref), _ref_key(n.ref), _pin_key(n.pin)))
    return WiringFinding(
        id="",
        pin=pin_ref,
        pin_name=pin.name,
        net=net,
        conflicts=[n.pin_ref for n in others],
        inductors=[],
        nets=[net],
        text=(
            f"{pin_ref} ({pin.name}) is {ref}'s feedback input ({_cited(pin)}), but "
            f"{problem}, so {ref} regulates against that net instead of its output. It "
            "should sense the output, directly or through a divider."
        ),
    )


def _shorted(
    design: Design, ref: str, pins: Mapping[str, Pin], part: AppPart, outputs: set[str]
) -> WiringFinding | None:
    """A required typical-application part two of whose IC pins (with
    different names) share a net on the board, so the board shorts it."""
    if not part.required or part.kind not in _SHORTED_KINDS:
        return None
    by_net: dict[str, list[Pin]] = {}
    for number in dict.fromkeys(part.pins):
        net = design.net_of(f"{ref}.{number}")
        if net is not None and number in pins:
            by_net.setdefault(net, []).append(pins[number])
    for net, joined in by_net.items():
        pair = next(
            (
                (a, b)
                for i, a in enumerate(joined)
                for b in joined[i + 1 :]
                if not _may_share(a, b, net, outputs)
            ),
            None,
        )
        if pair is None:
            continue
        a, b = pair
        value = f" {part.value}" if part.value else ""
        return WiringFinding(
            id="",
            pin=f"{ref}.{a.number}",
            pin_name=a.name,
            net=net,
            conflicts=[f"{ref}.{b.number}"],
            inductors=[],
            nets=[net],
            text=(
                f"{ref}'s datasheet puts its {part.role} ({part.kind}{value}, p.{part.page}) "
                f"between {ref}.{a.number} ({a.name}) and {ref}.{b.number} ({b.name}), but "
                f"both pins are on net {net}, so the {part.kind} is shorted out. The two "
                "pins should be on separate nets."
            ),
        )
    return None


def _may_share(a: Pin, b: Pin, net: str, outputs: set[str]) -> bool:
    """Pins a board may join: same name, a pin outside the power roles,
    the same supply or ground role, or a feedback pin on its own output
    (a fixed-output version)."""
    if not a.name or not b.name or _norm(a.name, a.number) == _norm(b.name, b.number):
        return True
    roles = {a.role, b.role}
    if not roles <= _POWER_ROLES:
        return True
    if len(roles) == 1 and roles <= _SUPPLY_ROLES | {"ground"}:
        return True
    return roles == {"feedback", "supply_out"} and net in outputs


def _joins(node: Node, ref: str, pin: Pin, pins: Mapping[str, Pin]) -> bool:
    """Whether ``node`` on a switch net is a pin that net must not reach:
    another IC's, or its own IC's under another name (repeats of one
    switch pin, SW on pins 1 and 2, are one node)."""
    if node.ref != ref:
        return is_ic(node.ref)
    other = pins.get(node.pin)
    if other is None or not other.name:
        return False
    return _norm(other.name, other.number) != _norm(pin.name, pin.number)


def _rail_nets(design: Design, ics: IcPins) -> dict[str, set[str]]:
    """Net -> the IC pins (REF.PIN) on it that make it a supply rail: a
    supply pin by datasheet or table, or by a name like VIN or VDD."""
    rails: dict[str, set[str]] = {}
    for ref, pins in ics.items():
        for number, pin in pins.items():
            if pin.role not in _SUPPLY_ROLES:
                continue
            if pin.source == BY_NAME and not _RAIL_PIN.fullmatch(_norm(pin.name, number)):
                continue
            if (net := design.net_of(f"{ref}.{number}")) is not None:
                rails.setdefault(net, set()).add(f"{ref}.{number}")
    return rails


def _is_rail(net: str, rails: Mapping[str, set[str]], pin_ref: str) -> bool:
    """Ground, a net named for a voltage, or one an IC's supply pin is on."""
    if is_ground(net) or named_voltage(net) is not None:
        return True
    return bool(rails.get(net, set()) - {pin_ref})


def _own_outputs(design: Design, ref: str, pins: Mapping[str, Pin]) -> set[str]:
    """The nets IC ``ref`` puts its output on: its supply-out pins' nets,
    the far end of an inductor on its switch pins (a buck has no output
    pin), and one hop on through a resistor, inductor or ferrite bead."""
    switch_nets = _nets_of(design, ref, pins, "switch")
    outputs = _nets_of(design, ref, pins, "supply_out")
    for net in switch_nets:
        for node in design.nets[net]:
            if is_inductor(design, node.ref):
                outputs |= _far_ends(design, node) - switch_nets
    for net in list(outputs):
        for node in design.nets[net]:
            if _ref_head(node.ref) in _OUTPUT_HOP and not is_ground(net):
                # Not back through its own inductor onto the switch node.
                far = _far_ends(design, node) - switch_nets
                outputs |= {n for n in far if not is_ground(n)}
    return outputs


def _nets_of(design: Design, ref: str, pins: Mapping[str, Pin], role: PinRole) -> set[str]:
    """The nets IC ``ref``'s pins of one role are on."""
    nets = (design.net_of(f"{ref}.{n}") for n, p in pins.items() if p.role == role)
    return {net for net in nets if net is not None}


def _far_ends(design: Design, node: Node) -> set[str]:
    """The nets a two-pin part's other pin is on."""
    pin = {"1": "2", "2": "1"}.get(node.pin)
    net = design.net_of(f"{node.ref}.{pin}") if pin else None
    return {net} if net is not None else set()


def _cited(pin: Pin) -> str:
    """Where the pin's role is from, as a finding says it."""
    if pin.source == BY_NAME:
        return f"by {BY_NAME}"
    if pin.source == BY_DATASHEET:
        return f"{BY_DATASHEET}: {pin.function}" if pin.function else BY_DATASHEET
    return pin.source


def _listed(nodes: Sequence[Node]) -> str:
    listed = [f"{n.pin_ref}({n.function})" if n.function else n.pin_ref for n in nodes]
    more = len(listed) - _MAX_LISTED
    return ", ".join(listed[:_MAX_LISTED]) + (f" and {more} more pins" if more > 0 else "")


# "Both +3V3 and SW2 are attached to the same items; +3V3 will be used
# in the netlist": KiCad's multiple_net_names wording.
_MERGED = re.compile(r"Both (.+?) and (.+?) are attached to the same items")


def label_causes(
    design: Design,
    ref: str,
    pin_name: str,
    net: str,
    erc: Sequence[ErcEntry],
    labels: Sequence[Label],
) -> list[str]:
    """Sentences naming each label the ERC reports merged into ``net`` on
    ``ref``'s sheet, the likely cause of a short there.

    The netlist can't show this: a label joined to a power net takes the
    power net's name and vanishes. It exists because a private board's L2
    pin sat on +3V3 through a second L2-node label placed on VOUT, and the
    model, seeing only the netlist, told the user to rewire the inductor.
    The ERC said so, but as one of 24 benign-looking warnings.
    """
    short = short_net_name(net)
    sheet = design.parts[ref].sheet if ref in design.parts else ""
    hits: list[tuple[ErcEntry, str]] = []
    for e in erc:
        m = _MERGED.match(e.message)
        if e.rule != "multiple_net_names" or not m or short not in m.groups():
            continue
        if sheet and e.sheet != sheet:
            continue  # a label on another sheet joined a global net, not this pin's
        hits.append((e, m.group(2) if m.group(1) == short else m.group(1)))
    # A label named after the flagged pin (SW_L2 for L2) is the telling
    # one; when there is one, the rest are naming noise.
    named = [h for h in hits if _names_pin(h[1], pin_name)]
    return [_cause(e, merged, short, pin_name, labels) for e, merged in (named or hits)[:3]]


def _names_pin(label: str, pin_name: str) -> bool:
    return pin_name.upper() in re.split(r"[^A-Z0-9]+", label.upper())


def _cause(e: ErcEntry, merged: str, short: str, pin_name: str, labels: Sequence[Label]) -> str:
    label = next((lb for lb in labels if lb.uuid in e.uuids and lb.name == merged), None)
    if label is None:
        return (
            f"Likely cause: {e.id} reports the label {merged} attached to {short} items "
            f"on sheet {e.sheet}; a misplaced label joins nets without any wire."
        )
    # Same-named labels join their wires: local and hierarchical ones within
    # their sheet, global ones across the whole design.
    peers = [
        lb
        for lb in labels
        if lb.name == merged
        and lb.kind == label.kind
        and lb.uuid != label.uuid
        and (lb.kind == "global_label" or lb.file == label.file)
    ]
    here = f"the {merged} label at ({label.x:g}, {label.y:g}) mm on {label.file}"
    if not peers:
        return (
            f"Likely cause: {e.id} reports {here} attached to {short} items; "
            "check that the label belongs there."
        )
    others = ", ".join(f"({lb.x:g}, {lb.y:g})" for lb in peers)
    return (
        f"Likely cause: {e.id} reports {here} attached to {short} items, and another "
        f"{merged} label sits at {others} mm. Same-named labels join their wires, so if "
        f"that one is on the {pin_name} node, this label is what shorts it to {short}: "
        "fix or remove the label, not the wiring."
    )


def _norm(name: str, number: str = "") -> str:
    """A pin name in upper-case letters and digits, without KiCad 10's
    ``_<pin number>`` suffix."""
    if number and name.endswith(f"_{number}"):
        name = name[: -len(number) - 1]
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _pin_key(number: str) -> tuple[bool, int, str]:
    return (not number.isdigit(), int(number) if number.isdigit() else 0, number)


def _ref_head(ref: str) -> str:
    return ref.rstrip("0123456789")


def _ref_key(ref: str) -> tuple[str, int]:
    head = _ref_head(ref)
    tail = ref[len(head) :]
    return head, int(tail) if tail else 0
