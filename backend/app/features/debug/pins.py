"""Hand-written pin table: what each pin of a few common ICs does, so
the model reads real pin functions instead of recalling them.

Each entry was copied from the part's datasheet pin-function table (the
document is named per part). Pin *numbers* key the table, because symbols
name pins differently from datasheets (the ESP32 module's symbol says
``GPIO4/TOUCH4/ADC1_CH3`` where the datasheet says ``IO4``). A part matches
on its value prefix and package, since pin numbers differ per package.

This is the MVP source of datasheet evidence. Per-part datasheet retrieval
replaces it (plan 001, "After the hackathon").
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.features.debug.design import Design, Label, short_net_name
from app.features.debug.schemas import ErcEntry, IcSummary, WiringFinding


@dataclass(frozen=True)
class PartPins:
    """One part's pin table. ``pins`` maps pin number to (datasheet name,
    function). ``inductor_pins`` are the pins the datasheet says connect
    only to the power inductor (switch nodes), which ``check_wiring`` holds
    to that."""

    value_prefix: str
    package_hint: str | None
    source: str
    pins: dict[str, tuple[str, str]]
    inductor_pins: frozenset[str] = frozenset()

    def matches(self, value: str, lib_part: str, footprint: str) -> bool:
        if not value.upper().startswith(self.value_prefix):
            return False
        if self.package_hint is None:
            return True
        haystack = f"{value} {lib_part} {footprint}".upper()
        return self.package_hint in haystack


_TPS63070 = PartPins(
    value_prefix="TPS63070",
    package_hint=None,  # one package (RNM, VQFN-HR 15)
    source="TI TPS63070 datasheet SLVSC58B, Pin Functions",
    pins={
        "1": (
            "PS/SYNC",
            "Low = forced PWM; high = PWM/PFM power-save mode; a clock synchronizes "
            "the switching frequency.",
        ),
        "2": ("PG", "Open-drain power-good output."),
        "3": ("VAUX", "Capacitor for the internal regulator. Must not be loaded externally."),
        "4": ("GND", "Control/logic ground."),
        "5": (
            "FB",
            "Voltage feedback on adjustable versions; connect to VOUT on fixed versions.",
        ),
        "6": (
            "FB2",
            "Voltage-scaling output: a resistor from FB to FB2 changes the divider "
            "ratio when VSEL is high. Leave open or tie to GND if unused.",
        ),
        "7": ("VOUT", "Buck-boost converter output."),
        "8": ("VOUT", "Buck-boost converter output."),
        "9": ("L2", "Inductor connection (output side of the power stage)."),
        "10": ("PGND", "Power ground."),
        "11": ("L1", "Inductor connection (input side of the power stage)."),
        "12": ("VIN", "Supply voltage for the power stage."),
        "13": ("VIN", "Supply voltage for the power stage."),
        "14": ("EN", "Enable input: high enables the device, low disables it."),
        "15": ("VSEL", "Voltage-scaling input: high turns on a transistor pulling FB2 to GND."),
    },
    inductor_pins=frozenset({"9", "11"}),
)

_TPS2121 = PartPins(
    value_prefix="TPS2121",
    package_hint="RUX",  # VQFN-HR 12; the WCSP numbering is different
    source="TI TPS2120/TPS2121 datasheet SLVSEA3F, Pin Functions (VQFN-HR)",
    pins={
        "1": ("OUT", "Power output."),
        "2": ("IN2", "Power input for source 2."),
        "3": (
            "CP2",
            "Comparator input, compared with PR1 to set the switchover voltage. "
            "Tie to GND if unused.",
        ),
        "4": (
            "OV2",
            "Active-low enable of the IN2 overvoltage supervisor. Tie to GND if unused.",
        ),
        "5": (
            "OV1",
            "Active-low enable of the IN1 overvoltage supervisor. Tie to GND if unused.",
        ),
        "6": (
            "PR1",
            "Enables priority operation; connect to IN1 (divider) to set the switchover "
            "voltage. Tie to GND if unused.",
        ),
        "7": ("IN1", "Power input for source 1."),
        "8": ("OUT", "Power output."),
        "9": ("ST", "Status output: which channel is selected. Tie to GND if unused."),
        "10": ("ILIM", "Output current limit setting for both channels."),
        "11": ("SS", "Sets the input settling delay and output soft-start time."),
        "12": ("GND", "Device ground."),
    },
)

_TPS3839 = PartPins(
    value_prefix="TPS3839",
    package_hint="DBZ",  # SOT-23-3; the X2SON numbering is different
    source="TI TPS3839 datasheet SBVS193D, Pin Functions (SOT23-3)",
    pins={
        "1": ("GND", "Ground."),
        "2": (
            "RESET",
            "Active-low push-pull reset output. Low while VDD is below the factory "
            "threshold, and for the delay time after VDD rises above it.",
        ),
        "3": ("VDD", "Supply voltage; also the monitored voltage."),
    },
)


def _esp32_s3_wroom_1() -> PartPins:
    gpio = "General-purpose I/O"
    pins: dict[str, tuple[str, str]] = {
        "1": ("GND", "Ground."),
        "2": ("3V3", "Power supply."),
        "3": (
            "EN",
            "Chip enable: high = on, low = chip powered off. Must not be left floating.",
        ),
        "27": (
            "IO0",
            "GPIO0. Strapping pin (boot mode, with GPIO46); internal pull-up at reset.",
        ),
        "15": ("IO3", "GPIO3. Strapping pin (JTAG signal source); floating by default."),
        "26": (
            "IO45",
            "GPIO45. Strapping pin (VDD_SPI voltage); internal pull-down at reset.",
        ),
        "16": (
            "IO46",
            "GPIO46. Strapping pin (boot mode, ROM message printing); internal pull-down at reset.",
        ),
        "13": ("IO19", f"{gpio}; also USB_D- of the built-in USB."),
        "14": ("IO20", f"{gpio}; also USB_D+ of the built-in USB."),
        "36": ("RXD0", "U0RXD (UART0 receive, the default console), GPIO44."),
        "37": ("TXD0", "U0TXD (UART0 transmit, the default console), GPIO43."),
        "28": ("IO35", f"{gpio}. Not available on modules with octal PSRAM."),
        "29": ("IO36", f"{gpio}. Not available on modules with octal PSRAM."),
        "30": ("IO37", f"{gpio}. Not available on modules with octal PSRAM."),
        "40": ("GND", "Ground."),
        "41": ("EPAD", "Ground (exposed pad)."),
    }
    others = {
        "4": "IO4",
        "5": "IO5",
        "6": "IO6",
        "7": "IO7",
        "8": "IO15",
        "9": "IO16",
        "10": "IO17",
        "11": "IO18",
        "12": "IO8",
        "17": "IO9",
        "18": "IO10",
        "19": "IO11",
        "20": "IO12",
        "21": "IO13",
        "22": "IO14",
        "23": "IO21",
        "24": "IO47",
        "25": "IO48",
        "31": "IO38",
        "32": "IO39",
        "33": "IO40",
        "34": "IO41",
        "35": "IO42",
        "38": "IO2",
        "39": "IO1",
    }
    for number, name in others.items():
        pins.setdefault(number, (name, f"{gpio}."))
    return PartPins(
        value_prefix="ESP32-S3-WROOM-1",
        package_hint=None,
        source="Espressif ESP32-S3-WROOM-1 datasheet v1.3, Table 3 and 3.3 Strapping Pins",
        pins=pins,
    )


PIN_TABLE: tuple[PartPins, ...] = (_TPS63070, _TPS2121, _TPS3839, _esp32_s3_wroom_1())


@dataclass(frozen=True)
class PinLookup:
    """The pin table applied to one design."""

    ics: list[IcSummary]
    pin_refs: frozenset[str]  # REF.PIN covered by the table, for the checker
    text: str  # what the model reads
    findings: list[WiringFinding]  # from check_wiring, verified


def is_ic(ref: str) -> bool:
    """Reference designators KiCad uses for ICs (U1, IC3)."""
    head = ref.rstrip("0123456789")
    return head in {"U", "IC"} and head != ref


def lookup(design: Design, erc: Sequence[ErcEntry] = (), labels: Sequence[Label] = ()) -> PinLookup:
    """Match every IC on the board against the pin table. ``erc`` and
    ``labels`` let a wiring finding name the label behind it."""
    ics: list[IcSummary] = []
    pin_refs: set[str] = set()
    lines: list[str] = []
    findings: list[WiringFinding] = []
    used_pins = {n.pin_ref for nodes in design.nets.values() for n in nodes}
    for part in sorted(design.parts.values(), key=lambda p: _ref_key(p.ref)):
        if not is_ic(part.ref):
            continue
        entry = next(
            (e for e in PIN_TABLE if e.matches(part.value, part.lib_part, part.footprint)), None
        )
        ics.append(
            IcSummary(
                ref=part.ref, value=part.value, footprint=part.footprint, pin_data=entry is not None
            )
        )
        if entry is None:
            continue
        findings += check_wiring(design, part.ref, entry, erc, labels)
        lines.append(f"{part.ref} ({part.value}), from {entry.source}:")
        for number, (name, function) in sorted(entry.pins.items(), key=lambda kv: int(kv[0])):
            pin_ref = f"{part.ref}.{number}"
            if pin_ref not in used_pins:
                continue
            pin_refs.add(pin_ref)
            lines.append(f"  {pin_ref} {name}: {function}")
    missing = [ic.ref for ic in ics if not ic.pin_data]
    if missing:
        lines.append(f"No pin table for: {', '.join(missing)}.")
    findings = [f.model_copy(update={"id": f"wf{i}"}) for i, f in enumerate(findings, start=1)]
    return PinLookup(
        ics=ics,
        pin_refs=frozenset(pin_refs),
        text="\n".join(lines) + "\n",
        findings=findings,
    )


# How many other pins a finding names before it says "and N more": a
# shorted power rail can carry 40-odd pins.
_MAX_LISTED = 6


def check_wiring(
    design: Design,
    ref: str,
    entry: PartPins,
    erc: Sequence[ErcEntry] = (),
    labels: Sequence[Label] = (),
) -> list[WiringFinding]:
    """Flag each inductor pin of ``ref`` whose net reaches anything but an
    inductor (or a test point), or no inductor at all. When the ERC shows a
    label merged into that net, the finding names it as the likely cause.

    Deterministic, from the netlist and the pin table only, so a finding is
    verified, not the model's reasoning. It exists because the model saw an
    L2 pin on the output rail, but treated "a switch node must
    be its own net" as recalled topology and ranked the short low.
    """
    findings: list[WiringFinding] = []
    for number in sorted(entry.inductor_pins, key=int):
        pin_ref = f"{ref}.{number}"
        net = design.net_of(pin_ref)
        if net is None:
            continue  # unconnected: KiCad's ERC reports that
        others = [n for n in design.nets[net] if n.pin_ref != pin_ref]
        inductors = [n for n in others if is_inductor(design, n.ref)]
        foreign = [n for n in others if not is_inductor(design, n.ref) and _ref_head(n.ref) != "TP"]
        if not foreign and inductors:
            continue
        name = entry.pins[number][0]
        # The IC's own pins first, then other ICs': L2 on VOUT is the
        # telling part, not the decoupling capacitors.
        foreign.sort(key=lambda n: (n.ref != ref, not is_ic(n.ref), _ref_key(n.ref), n.pin))
        listed = [f"{n.pin_ref}({n.function})" if n.function else n.pin_ref for n in foreign]
        more = len(listed) - _MAX_LISTED
        shown = ", ".join(listed[:_MAX_LISTED]) + (f" and {more} more pins" if more > 0 else "")
        if foreign:
            problem = f"but its net {net} also connects {shown}"
        else:
            problem = f"but its net {net} has no inductor on it"
        findings.append(
            WiringFinding(
                id="",
                pin=pin_ref,
                pin_name=name,
                net=net,
                conflicts=[n.pin_ref for n in foreign],
                inductors=[n.pin_ref for n in inductors],
                text=(
                    f"{pin_ref} ({name}) is an inductor connection ({entry.source}), "
                    f"{problem}. It should connect only to its inductor."
                    + "".join(
                        f" {cause}" for cause in label_causes(design, ref, name, net, erc, labels)
                    )
                ),
            )
        )
    return findings


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


def format_findings(findings: list[WiringFinding]) -> str:
    """The findings as the model reads them, with ids."""
    if not findings:
        return "(none: no inductor pin in the pin table shares a net with anything else)\n"
    return "\n".join(f"{f.id}: {f.text}" for f in findings) + "\n"


def is_inductor(design: Design, ref: str) -> bool:
    """An inductor by designator (L3) or by symbol (Device:L_Small)."""
    part = design.parts.get(ref)
    lib_name = part.lib_part.rsplit(":", 1)[-1].upper() if part else ""
    return _ref_head(ref) == "L" or lib_name == "L" or lib_name.startswith("L_")


def _ref_head(ref: str) -> str:
    return ref.rstrip("0123456789")


def _ref_key(ref: str) -> tuple[str, int]:
    head = _ref_head(ref)
    tail = ref[len(head) :]
    return head, int(tail) if tail else 0
