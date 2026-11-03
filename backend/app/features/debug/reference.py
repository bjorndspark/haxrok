"""Reference designs: what the manufacturer's own documents say a correct
circuit around a few common ICs looks like, and values the backend
calculates from the netlist with the datasheet's formulas.

Two outputs, both verified rather than recalled:

- **Notes** (rd1, …): short facts copied from the manufacturer's datasheet,
  application note or hardware design guide, each with its section. The
  model reads them so it stops calling a reference circuit wrong (in the
  no-fault eval it flagged the RP2040's 1 kΩ crystal resistor, which is the
  Raspberry Pi design) and can cite them.
- **Calculations** (cf1, …): a regulator's output voltage from its feedback
  divider, a charger's current from its program resistor. Deterministic
  arithmetic on resistor values in the netlist. A regulator whose computed
  output disagrees with its output net's name ("+3V3") is flagged.

Like ``pins.PIN_TABLE`` this is a hand-written MVP source; per-part
datasheet retrieval replaces it (plan 001, "After the hackathon"). Every
entry here was read in the named document, not recalled.
"""

import re
from dataclasses import dataclass

from app.features.debug.design import Design
from app.features.debug.schemas import Calculation, ReferenceNote

# --- notes -----------------------------------------------------------------------


@dataclass(frozen=True)
class PartReference:
    """The reference notes for one part, matched on its value prefix."""

    value_prefix: str
    source: str
    notes: tuple[str, ...]

    def matches(self, value: str) -> bool:
        return value.upper().startswith(self.value_prefix)


_RP2040 = PartReference(
    value_prefix="RP2040",
    source="Raspberry Pi, Hardware design with RP2040, 2. Minimal design example",
    notes=(
        "Crystal (2.3): a 12 MHz ABM8-272-T3 between XIN and XOUT, a 15 pF capacitor "
        "from each side of the crystal to ground, and a 1 kΩ series resistor on XOUT. "
        "The 1 kΩ resistor is intended: it keeps the crystal from being over-driven at "
        "IOVDD = 3.3 V. With IOVDD below 3.3 V a smaller resistor is needed.",
        "Internal regulator (2.1.3): VREG_OUT connects to the DVDD pins; 1 µF capacitors "
        "close to both VREG_IN and VREG_OUT.",
        "Decoupling (2.1.2): 100 nF per power pin is the recommendation; the minimal "
        "design shares one capacitor between pins 48 and 49 as a deliberate trade-off.",
        "Flash (2.2): QSPI_SS has an optional 10 kΩ pull-up to 3.3 V (not fitted in the "
        "minimal design) and a 1 kΩ resistor to a USB_BOOT jumper to ground. Pulling "
        "QSPI_SS low at reset enters BOOTSEL (USB mass storage) mode. The 1 kΩ lets "
        "QSPI_SS over-drive the pull-down once booting.",
        "USB (2.4.1): USB_DP and USB_DM need 27 Ω series resistors close to the chip and "
        "no external pull-ups or pull-downs; those are built in.",
    ),
)

_MCP73831 = PartReference(
    value_prefix="MCP7383",
    source="Microchip MCP73831/2 datasheet DS20001984H, 3.0 Pin Descriptions and 5.0",
    notes=(
        "VDD (3.1): bypass to VSS with at least 4.7 µF; recommended supply VREG + 0.3 V to 6 V.",
        "VBAT (3.2): bypass to VSS with at least 4.7 µF for loop stability when the "
        "battery is disconnected.",
        "PROG (3.5): a resistor from PROG to VSS sets the charge current, "
        "IREG (mA) = 1000 V / RPROG (kΩ). Leaving PROG floating disables the charger.",
        "STAT (3.3): drives a charge-status LED, or takes a pull-up for a microcontroller "
        "input. Tri-state on the MCP73831, open-drain on the MCP73832.",
    ),
)

_SY8088 = PartReference(
    value_prefix="SY8088",
    source="Silergy AN_SY8088 Rev. 0.1, Pin Description and Application Information",
    notes=(
        "EN (pin 1): pull high to turn on. Do not leave it floating.",
        "FB (pin 5): the centre of the output divider; VOUT = 0.6 V × (1 + R1/R2), R1 from "
        "VOUT to FB and R2 from FB to GND, both 100 kΩ to 1 MΩ. An optional 10-47 pF "
        "capacitor across R1 speeds up the transient response.",
        "IN (pin 4): at least 4.7 µF ceramic to GND, close to the pins. Output capacitor: "
        "X7R, at least 4.7 µF.",
        "LX (pin 3): connects only to the switching end of the inductor.",
    ),
)

_ESP32_C3_MINI = PartReference(
    value_prefix="ESP32-C3-MINI",
    source="Espressif ESP32-C3-MINI-1 datasheet v2.2, 4 Boot Configurations and 9",
    notes=(
        "Strapping pins (4.1): GPIO2, GPIO8 and GPIO9. SPI boot (run from flash) needs "
        "GPIO9 = 1 (it has a weak internal pull-up) and GPIO2 = 1; download mode is "
        "GPIO8 = 1 with GPIO9 = 0. GPIO2 and GPIO8 float by default, and pulling GPIO2 up "
        "is recommended. GPIO8 also gates ROM message printing.",
        "EN (9, Peripheral Schematics): do not leave EN floating; an RC delay of "
        "R = 10 kΩ and C = 1 µF on EN is the usual recommendation so the supply is "
        "stable before the chip starts.",
    ),
)

REFERENCES: tuple[PartReference, ...] = (_RP2040, _MCP73831, _SY8088, _ESP32_C3_MINI)


# --- calculations ------------------------------------------------------------------


@dataclass(frozen=True)
class FeedbackRule:
    """An adjustable regulator: VOUT = vref × (1 + R_top/R_bottom)."""

    value_prefix: str
    fb_pin: str
    vref: float
    source: str


@dataclass(frozen=True)
class ProgramRule:
    """A charger whose current is ``k / R`` with R in kΩ and the result in mA."""

    value_prefix: str
    prog_pin: str
    k: float
    source: str


FEEDBACK_RULES: tuple[FeedbackRule, ...] = (
    FeedbackRule("SY8088", "5", 0.6, "Silergy AN_SY8088, FB pin: VOUT = 0.6 V × (1 + R1/R2)"),
)

PROGRAM_RULES: tuple[ProgramRule, ...] = (
    # SOT-23-5 numbering (the package hobby boards use): PROG is pin 5.
    ProgramRule(
        "MCP7383", "5", 1000.0, "Microchip DS20001984H 5.0: IREG (mA) = 1000 V / RPROG (kΩ)"
    ),
)

# How far a computed output may sit from the net's named voltage before it
# is flagged. 1% resistors and a ±2% reference land well inside 8%.
_TOLERANCE = 0.08

_GROUND = re.compile(r"^(A|D|P)?GND|^VSS|^0V$", re.IGNORECASE)

# "100k", "22k1", "4k7", "470R", "0R", "1M", "2.2k", optionally with R/Ω after.
_VALUE = re.compile(r"(\d+(?:\.\d+)?)([RKM]?)(\d*)(?:R|Ω|OHMS?)?", re.IGNORECASE)
_MULT = {"": 1.0, "R": 1.0, "K": 1e3, "M": 1e6}


def parse_ohms(value: str) -> float | None:
    """A resistor's value in ohms, from KiCad's value field ("22k1R 1%").
    None for anything else (DNP, empty, a part number)."""
    token = value.strip().split(" ")[0] if value.strip() else ""
    m = _VALUE.fullmatch(token)
    if not m:
        return None
    whole, mult, frac = m.groups()
    if frac and "." in whole:
        return None
    number = float(f"{whole}.{frac}" if frac else whole)
    return number * _MULT[mult.upper()]


def named_voltage(net: str) -> float | None:
    """The voltage a net's name promises: "+3V3" → 3.3, "/5V" → 5, "1.8V" → 1.8."""
    name = net.rstrip("/").split("/")[-1].lstrip("+")
    m = re.fullmatch(r"(?:V[A-Z_]*)?(\d+)V(\d+)", name, re.IGNORECASE) or re.fullmatch(
        r"(\d+(?:\.\d+)?)V", name, re.IGNORECASE
    )
    if not m:
        return None
    return float(".".join(g for g in m.groups() if g))


def format_ohms(ohms: float) -> str:
    if ohms >= 1e6:
        return f"{ohms / 1e6:g} MΩ"
    if ohms >= 1e3:
        return f"{ohms / 1e3:g} kΩ"
    return f"{ohms:g} Ω"


def _resistors_on(design: Design, net: str) -> list[tuple[str, str, float]]:
    """(resistor ref, the net at its other end, ohms) for each two-pin
    resistor with a parseable value on ``net``."""
    found = []
    for node in design.nets.get(net, []):
        part = design.parts.get(node.ref)
        if part is None or node.ref.rstrip("0123456789") != "R":
            continue
        ohms = parse_ohms(part.value)
        other_pin = "2" if node.pin == "1" else "1"
        other = design.net_of(f"{node.ref}.{other_pin}")
        if ohms is not None and other is not None:
            found.append((node.ref, other, ohms))
    return found


def _feedback(design: Design, ref: str, rule: FeedbackRule) -> Calculation | None:
    fb_ref = f"{ref}.{rule.fb_pin}"
    fb_net = design.net_of(fb_ref)
    if fb_net is None:
        return None
    resistors = _resistors_on(design, fb_net)
    bottom = [r for r in resistors if _GROUND.match(r[1].lstrip("/"))]
    top = [r for r in resistors if not _GROUND.match(r[1].lstrip("/"))]
    if len(top) != 1 or len(bottom) != 1 or bottom[0][2] == 0:
        return None  # not a plain two-resistor divider; leave it to the model
    (top_ref, out_net, r_top), (bottom_ref, _, r_bottom) = top[0], bottom[0]
    vout = rule.vref * (1 + r_top / r_bottom)
    expected = named_voltage(out_net)
    flagged = expected is not None and abs(vout - expected) > _TOLERANCE * expected
    text = (
        f"{ref} feedback divider: {top_ref} ({format_ohms(r_top)}) from {out_net} to FB "
        f"and {bottom_ref} ({format_ohms(r_bottom)}) from FB to ground set the output to "
        f"{rule.vref:g} V × (1 + {format_ohms(r_top)}/{format_ohms(r_bottom)}) = {vout:.2f} V "
        f"({rule.source})."
    )
    if expected is not None:
        text += (
            f" The output net {out_net} is named for {expected:g} V, so the divider is wrong."
            if flagged
            else f" That matches the {expected:g} V the net {out_net} is named for."
        )
    return Calculation(
        id="",
        kind="regulator_output",
        ic=ref,
        pins=[fb_ref, f"{top_ref}.1", f"{top_ref}.2", f"{bottom_ref}.1", f"{bottom_ref}.2"],
        value=round(vout, 3),
        unit="V",
        expected=expected,
        flagged=flagged,
        text=text,
    )


def _program(design: Design, ref: str, rule: ProgramRule) -> Calculation | None:
    prog_ref = f"{ref}.{rule.prog_pin}"
    prog_net = design.net_of(prog_ref)
    if prog_net is None:
        return None
    to_ground = [
        r for r in _resistors_on(design, prog_net) if _GROUND.match(r[1].lstrip("/")) and r[2] > 0
    ]
    if len(to_ground) != 1:
        return None
    r_ref, _, ohms = to_ground[0]
    current = rule.k / (ohms / 1e3)
    return Calculation(
        id="",
        kind="charge_current",
        ic=ref,
        pins=[prog_ref, f"{r_ref}.1", f"{r_ref}.2"],
        value=round(current, 1),
        unit="mA",
        expected=None,
        flagged=False,
        text=(
            f"{ref} charge current: {r_ref} ({format_ohms(ohms)}) from PROG to ground sets "
            f"{rule.k:g} V / {ohms / 1e3:g} kΩ = {current:.0f} mA ({rule.source})."
        ),
    )


@dataclass(frozen=True)
class ReferenceLookup:
    """The reference notes and calculations for one design."""

    notes: list[ReferenceNote]
    calculations: list[Calculation]
    text: str  # what the model reads


def lookup(design: Design) -> ReferenceLookup:
    """Match every part against the reference notes and calculation rules."""
    notes: list[ReferenceNote] = []
    calcs: list[Calculation] = []
    seen_parts: set[str] = set()
    for part in sorted(design.parts.values(), key=lambda p: p.ref):
        value = part.value.upper()
        entry = next((r for r in REFERENCES if r.matches(value)), None)
        # One set of notes per part type: three RP2040s don't need three copies.
        if entry is not None and entry.value_prefix not in seen_parts:
            seen_parts.add(entry.value_prefix)
            for text in entry.notes:
                notes.append(
                    ReferenceNote(
                        id=f"rd{len(notes) + 1}", part=part.value, source=entry.source, text=text
                    )
                )
        for fb in FEEDBACK_RULES:
            if value.startswith(fb.value_prefix) and (c := _feedback(design, part.ref, fb)):
                calcs.append(c)
        for prog in PROGRAM_RULES:
            if value.startswith(prog.value_prefix) and (c := _program(design, part.ref, prog)):
                calcs.append(c)
    calcs = [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(calcs, start=1)]
    return ReferenceLookup(notes=notes, calculations=calcs, text=_format(notes, calcs))


def _format(notes: list[ReferenceNote], calcs: list[Calculation]) -> str:
    lines = ["### Reference notes (copied from the manufacturer's documents)"]
    lines += [f"{n.id} [{n.part}, {n.source}]: {n.text}" for n in notes] or [
        "(none for the parts on this board)"
    ]
    lines += ["", "### Calculated from the netlist (verified arithmetic)"]
    lines += [f"{c.id}{' FLAGGED' if c.flagged else ''}: {c.text}" for c in calcs] or [
        "(none: no regulator divider or charger program resistor recognised)"
    ]
    return "\n".join(lines) + "\n"
