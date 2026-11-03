"""Values the backend calculates from the netlist, before the model runs.

Each is exact arithmetic on part values in the netlist, so the model and
the UI can treat it as verified (``Calculation``, ids cf1, …). A few are
flagged, when the netlist as drawn contradicts itself or misses something
every such circuit needs:

- **regulator_output**: an adjustable regulator's output from its
  feedback divider, with the datasheet formula. Flagged when it is more
  than 8% from the voltage its output net is named for (+3V3 set to 0.73 V).
- **charge_current**: a charger's current from its program resistor.
- **dropout**: a fixed linear regulator's input minus its output, against
  the datasheet dropout. Flagged when the input rail's named voltage leaves
  less than the typical dropout (an AMS1117-3.3 on a 3.7 V rail).
- **led_current**: rail → resistor → LED → ground, as a range over typical
  forward voltages. Flagged when an LED sits across a rail with no resistor.
- **i2c_pullup**: the pull-up on each SDA/SCL net. Flagged when there is
  none on this board (it may sit on a connected module; the text says so).
- **rc_delay**: R×C on an enable or reset pin with a pull-up and a
  capacitor to ground.
- **divider**: a two-resistor divider into an IC pin: the voltage there, or
  the ratio when the rail's name carries no voltage.
- **decoupling**: the capacitors to ground on each IC's supply rails.
  Flagged when a rail has none at all; fewer capacitors than the IC has
  power pins on the rail is reported without a flag (one shared capacitor
  can be a deliberate trade-off, as in the RP2040 minimal design).
- **usb_cc**: the Rd pull-downs on a USB-C receptacle's CC1 and CC2. Flagged
  when the two pins share one resistor, a pin has none, or one isn't
  5.1 kΩ ±10% (USB Type-C Specification, Rd for a sink).

The formulas for specific parts (feedback, program resistor) were read in
the named document; see ``reference.py`` for the notes from the same
sources.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.features.debug.design import Design, Node
from app.features.debug.schemas import Calculation

# --- value parsing --------------------------------------------------------------

# "100k", "22k1", "4k7", "470R", "0R", "1M", "2.2k", optionally with R/Ω after.
_OHMS = re.compile(r"(\d+(?:\.\d+)?)([RKM]?)(\d*)(?:R|Ω|OHMS?)?", re.IGNORECASE)
_OHM_MULT = {"": 1.0, "R": 1.0, "K": 1e3, "M": 1e6}

# "100n", "100nF", "4u7", "4.7uF", "1µF", "10p", "1uF/16V".
_FARADS = re.compile(r"(\d+(?:\.\d+)?)([PNUµM])(\d*)F?", re.IGNORECASE)
_FARAD_MULT = {"P": 1e-12, "N": 1e-9, "U": 1e-6, "µ": 1e-6, "M": 1e-6}


def _first_token(value: str) -> str:
    return re.split(r"[\s/,]", value.strip(), maxsplit=1)[0] if value.strip() else ""


def parse_ohms(value: str) -> float | None:
    """A resistor's value in ohms, from KiCad's value field ("22k1R 1%").
    None for anything else (DNP, empty, a part number)."""
    m = _OHMS.fullmatch(_first_token(value))
    if not m:
        return None
    whole, mult, frac = m.groups()
    if frac and "." in whole:
        return None
    return float(f"{whole}.{frac}" if frac else whole) * _OHM_MULT[mult.upper()]


def parse_farads(value: str) -> float | None:
    """A capacitor's value in farads ("100n", "4u7", "1uF/16V")."""
    m = _FARADS.fullmatch(_first_token(value))
    if not m:
        return None
    whole, mult, frac = m.groups()
    if frac and "." in whole:
        return None
    key = mult if mult == "µ" else mult.upper()
    return float(f"{whole}.{frac}" if frac else whole) * _FARAD_MULT[key]


def named_voltage(net: str) -> float | None:
    """The voltage a net's name promises: "+3V3" → 3.3, "/5V" → 5, "1.8V" → 1.8,
    "VBUS" → 5 (USB fixes it)."""
    name = net.rstrip("/").split("/")[-1].lstrip("+")
    if name.upper() in {"VBUS", "USB_VBUS", "VUSB"}:
        return 5.0
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


def format_farads(farads: float) -> str:
    for unit, scale in (("µF", 1e-6), ("nF", 1e-9)):
        if farads >= scale:
            return f"{farads / scale:g} {unit}"
    return f"{farads / 1e-12:g} pF"


def format_seconds(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:.2g} s"
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3g} ms"
    return f"{seconds * 1e6:.3g} µs"


# --- netlist helpers --------------------------------------------------------------

_GROUND = re.compile(r"^(A|D|P)?GND|^VSS|^0V$", re.IGNORECASE)


def _short(net: str) -> str:
    return net.rstrip("/").split("/")[-1]


def is_ground(net: str) -> bool:
    return _GROUND.match(_short(net)) is not None


def _head(ref: str) -> str:
    return ref.rstrip("0123456789")


def _ic_nodes(design: Design, net: str) -> list[Node]:
    return [n for n in design.nets.get(net, []) if _head(n.ref) in {"U", "IC"}]


@dataclass(frozen=True)
class _Two:
    """A two-pin part on a net: its ref, the net at its other end, its value."""

    ref: str
    other: str
    value: float


def _two_pin_on(
    design: Design, net: str, head: str, parse: Callable[[str], float | None]
) -> list[_Two]:
    found = []
    for node in design.nets.get(net, []):
        part = design.parts.get(node.ref)
        if part is None or _head(node.ref) != head:
            continue
        value = parse(part.value)
        other_pin = "2" if node.pin == "1" else "1"
        other = design.net_of(f"{node.ref}.{other_pin}")
        if value is not None and other is not None and other != net:
            found.append(_Two(node.ref, other, value))
    return found


def _resistors(design: Design, net: str) -> list[_Two]:
    return _two_pin_on(design, net, "R", parse_ohms)


def _capacitors(design: Design, net: str) -> list[_Two]:
    return _two_pin_on(design, net, "C", parse_farads)


def _pins_of(*parts: _Two) -> list[str]:
    return [f"{p.ref}.{n}" for p in parts for n in ("1", "2")]


# --- part-specific formulas --------------------------------------------------------


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


@dataclass(frozen=True)
class DropoutRule:
    """A fixed linear regulator: it needs VIN − VOUT ≥ its dropout."""

    value_prefix: str
    vin_pin: str
    vout_pin: str
    typical: float
    maximum: float
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

DROPOUT_RULES: tuple[DropoutRule, ...] = (
    # SOT-223 numbering: 1 GND/ADJ, 2 VOUT, 3 VIN.
    DropoutRule(
        "AMS1117",
        "3",
        "2",
        1.1,
        1.3,
        "AMS ds1117, Electrical Characteristics: dropout 1.1 V typical, 1.3 V max at 0.8 A",
    ),
)

# How far a computed output may sit from the net's named voltage before it
# is flagged. 1% resistors and a ±2% reference land well inside 8%.
_TOLERANCE = 0.08


def _feedback(design: Design, ref: str, rule: FeedbackRule) -> Calculation | None:
    fb_ref = f"{ref}.{rule.fb_pin}"
    fb_net = design.net_of(fb_ref)
    if fb_net is None:
        return None
    resistors = _resistors(design, fb_net)
    bottom = [r for r in resistors if is_ground(r.other)]
    top = [r for r in resistors if not is_ground(r.other)]
    if len(top) != 1 or len(bottom) != 1 or bottom[0].value == 0:
        return None  # not a plain two-resistor divider; leave it to the model
    t, b = top[0], bottom[0]
    vout = rule.vref * (1 + t.value / b.value)
    expected = named_voltage(t.other)
    flagged = expected is not None and abs(vout - expected) > _TOLERANCE * expected
    text = (
        f"{ref} feedback divider: {t.ref} ({format_ohms(t.value)}) from {t.other} to FB "
        f"and {b.ref} ({format_ohms(b.value)}) from FB to ground set the output to "
        f"{rule.vref:g} V × (1 + {format_ohms(t.value)}/{format_ohms(b.value)}) = {vout:.2f} V "
        f"({rule.source})."
    )
    if expected is not None:
        text += (
            f" The output net {t.other} is named for {expected:g} V, so the divider is wrong."
            if flagged
            else f" That matches the {expected:g} V the net {t.other} is named for."
        )
    return Calculation(
        id="",
        kind="regulator_output",
        ic=ref,
        pins=[fb_ref, *_pins_of(t, b)],
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
    to_ground = [r for r in _resistors(design, prog_net) if is_ground(r.other) and r.value > 0]
    if len(to_ground) != 1:
        return None
    r = to_ground[0]
    current = rule.k / (r.value / 1e3)
    return Calculation(
        id="",
        kind="charge_current",
        ic=ref,
        pins=[prog_ref, *_pins_of(r)],
        value=round(current, 1),
        unit="mA",
        expected=None,
        flagged=False,
        text=(
            f"{ref} charge current: {r.ref} ({format_ohms(r.value)}) from PROG to ground sets "
            f"{rule.k:g} V / {r.value / 1e3:g} kΩ = {current:.0f} mA ({rule.source})."
        ),
    )


def _fixed_output(value: str, prefix: str) -> float | None:
    """The output voltage in a fixed regulator's value: "AMS1117-3.3" → 3.3,
    "AMS1117-3V3" → 3.3; None for the adjustable part."""
    suffix = value.upper().removeprefix(prefix).lstrip("-_ ").split()[0:1]
    if not suffix:
        return None
    try:
        return float(suffix[0])
    except ValueError:
        return named_voltage(suffix[0])


_BATTERY = re.compile(r"BAT", re.IGNORECASE)
_LIION_FULL = 4.2  # a single Li-ion/LiPo cell, fully charged


def _dropout(design: Design, ref: str, rule: DropoutRule) -> Calculation | None:
    vin_ref, vout_ref = f"{ref}.{rule.vin_pin}", f"{ref}.{rule.vout_pin}"
    vin_net, vout_net = design.net_of(vin_ref), design.net_of(vout_ref)
    if vin_net is None or vout_net is None:
        return None
    vout = _fixed_output(design.parts[ref].value, rule.value_prefix) or named_voltage(vout_net)
    if vout is None:
        return None
    vin = named_voltage(vin_net)
    battery = vin is None and _BATTERY.search(_short(vin_net)) is not None
    if battery:
        vin = _LIION_FULL
    if vin is None:
        return None
    headroom = vin - vout
    head = (
        f"{ref} ({design.parts[ref].value}) headroom: VIN on {vin_net} "
        f"({vin:g} V{' if it is one Li-ion cell, fully charged' if battery else ''}) − VOUT "
        f"{vout:g} V = {headroom:.2f} V, against a dropout of {rule.typical:g} V typical, "
        f"{rule.maximum:g} V maximum ({rule.source})."
    )
    if headroom < rule.typical:
        tail = (
            f" That is below the dropout, so the output sags below {vout:g} V and follows "
            "the input"
            + (" (and a cell spends most of its charge below 4.2 V)." if battery else ".")
            + " A low-dropout regulator suits this rail better."
        )
    elif headroom < rule.maximum:
        tail = " Within the maximum dropout: it may drop out near full load."
    else:
        tail = " Enough headroom."
    return Calculation(
        id="",
        kind="dropout",
        ic=ref,
        pins=[vin_ref, vout_ref],
        value=round(headroom, 2),
        unit="V",
        expected=rule.typical,
        flagged=headroom < rule.typical and not battery,
        text=head + tail,
    )


# --- generic circuits -------------------------------------------------------------

# Forward voltages of indicator LEDs, red to white/blue. A range, because
# the netlist rarely says the colour; the text says to check the datasheet.
_VF_LOW, _VF_HIGH = 1.8, 3.2


def _is_led(design: Design, ref: str) -> bool:
    part = design.parts.get(ref)
    if part is None or _head(ref) not in {"D", "LED"}:
        return False
    lib = part.lib_part.rsplit(":", 1)[-1].upper()
    pins = sum(1 for nodes in design.nets.values() for n in nodes if n.ref == ref)
    return lib.startswith("LED") and pins == 2


def _led_pins(design: Design, ref: str) -> tuple[str, str] | None:
    """(anode net, cathode net) from the pin functions, A/K."""
    anode = cathode = None
    for net, nodes in design.nets.items():
        for n in nodes:
            if n.ref == ref:
                if n.function.upper() in {"A", "ANODE"}:
                    anode = net
                elif n.function.upper() in {"K", "CATHODE"}:
                    cathode = net
    return (anode, cathode) if anode and cathode else None


def _led(design: Design, ref: str) -> Calculation | None:
    nets = _led_pins(design, ref)
    if nets is None:
        return None
    anode, cathode = nets
    pins = [f"{ref}.1", f"{ref}.2"]
    rail_v = named_voltage(anode)
    if rail_v is not None and is_ground(cathode):
        return Calculation(
            id="",
            kind="led_current",
            ic=ref,
            pins=pins,
            value=None,
            unit="mA",
            expected=None,
            flagged=True,
            text=(
                f"LED {ref} sits directly between {anode} and ground with no resistor, so "
                "nothing limits its current: it will overheat or overload the rail."
            ),
        )
    # Resistor on either side: rail -> R -> LED -> GND, or rail -> LED -> R -> GND.
    if rail_v is not None:
        side, series = cathode, [r for r in _resistors(design, cathode) if is_ground(r.other)]
        source = anode
    else:
        side, series = anode, [r for r in _resistors(design, anode) if named_voltage(r.other)]
        source = series[0].other if series else ""
        rail_v = named_voltage(source) if series else None
        if not is_ground(cathode):
            return None
    if rail_v is None or len(series) != 1 or series[0].value <= 0:
        return None
    # Other parts on the middle net (a GPIO, a transistor) change the
    # circuit; only a plain series chain gets a number.
    if len(design.nets.get(side, [])) != 2:
        return None
    r = series[0]
    high = max(0.0, (rail_v - _VF_LOW) / r.value * 1e3)
    low = max(0.0, (rail_v - _VF_HIGH) / r.value * 1e3)
    return Calculation(
        id="",
        kind="led_current",
        ic=ref,
        pins=[*pins, *_pins_of(r)],
        value=round(high, 1),
        unit="mA",
        expected=None,
        flagged=False,
        text=(
            f"LED {ref} with {r.ref} ({format_ohms(r.value)}) from {source}: "
            f"({rail_v:g} V − Vf) / {format_ohms(r.value)} = {low:.1f} to {high:.1f} mA for a "
            f"forward voltage of {_VF_HIGH:g} V (white/blue) to {_VF_LOW:g} V (red). "
            "Check the LED's rated current in its datasheet."
        ),
    )


_I2C_TOKEN = re.compile(r"^(SDA|SCL)\d*$", re.IGNORECASE)


def _i2c_line(design: Design, net: str) -> str | None:
    """'SDA' or 'SCL' if ``net`` is an I²C line: by a whole token of its name
    (IO8_SCL, SDA1) or of a pin name on it. QSPI_SCLK and 22_SCLK are not."""
    tokens = re.split(r"[_/\-. ]", _short(net).upper())
    if "I2C" in "".join(tokens) or any(t.startswith("I2C") for t in tokens):
        tokens = [t.replace("SCLK", "SCL").replace("SDAT", "SDA") for t in tokens]
    for t in tokens:
        if _I2C_TOKEN.match(t):
            return t[:3]
    for n in design.nets.get(net, []):
        for t in re.split(r"[_/\-. ]", n.function.upper()):
            if _I2C_TOKEN.match(t):
                return t[:3]
    return None


_SUPPLY_PIN = re.compile(r"^(VDD\w*|VCC\w*|IOVDD|VDDIO|DVDD|3V3|VIN)$", re.IGNORECASE)


def _supply(design: Design, ref: str) -> tuple[str, float] | None:
    """The lowest named supply rail on an IC's power pins, e.g. ("+3V3", 3.3)."""
    rails = [
        (net, v)
        for net, nodes in design.nets.items()
        for n in nodes
        if n.ref == ref and _SUPPLY_PIN.match(n.function) and (v := named_voltage(net))
    ]
    return min(rails, key=lambda r: r[1]) if rails else None


def _i2c(design: Design, net: str, line: str) -> Calculation | None:
    ics = _ic_nodes(design, net)
    if not ics:
        return None
    ups = [r for r in _resistors(design, net) if not is_ground(r.other) and r.value > 0]
    ic_pins = [n.pin_ref for n in ics]
    if not ups:
        connectors = sorted({n.ref for n in design.nets[net] if _head(n.ref) in {"J", "P", "CN"}})
        where = (
            f" unless a module on {', '.join(connectors)} provides one"
            if connectors
            else ", and nothing else on the net could provide one"
        )
        return Calculation(
            id="",
            kind="i2c_pullup",
            ic=ics[0].ref,
            pins=ic_pins,
            value=None,
            unit="Ω",
            expected=None,
            flagged=True,
            text=(
                f"I²C {line} net {net} has no pull-up resistor on this board{where}. I²C lines "
                "are open-drain and only go high through a pull-up; an IC's internal pull-up "
                "is usually too weak (check its datasheet)."
            ),
        )
    combined = 1 / sum(1 / r.value for r in ups)
    rails = sorted({r.other for r in ups})
    names = ", ".join(f"{r.ref} ({format_ohms(r.value)})" for r in ups)
    parallel = f", {format_ohms(round(combined))} in parallel" if len(ups) > 1 else ""
    text = f"I²C {line} net {net} is pulled up to {', '.join(rails)} by {names}{parallel}."
    # A pull-up rail above an IC's own supply drives its pin above VDD.
    pull_v = max((v for r in ups if (v := named_voltage(r.other)) is not None), default=None)
    refs = sorted({n.ref for n in ics})  # an IC can have two pins on one net
    supplies = [(ref, s) for ref in refs if (s := _supply(design, ref))]
    over = [(ref, s) for ref, s in supplies if pull_v is not None and pull_v > s[1] + 0.3]
    if over:
        listed = ", ".join(f"{ref} (supply {rail}, {v:g} V)" for ref, (rail, v) in over)
        text += (
            f" That rail is {pull_v:g} V, above the supply of {listed}, so the line idles at "
            f"{pull_v:g} V on pins powered from a lower rail."
        )
    return Calculation(
        id="",
        kind="i2c_pullup",
        ic=ics[0].ref,
        pins=[*ic_pins, *_pins_of(*ups)],
        value=round(combined),
        unit="Ω",
        expected=None,
        flagged=bool(over),
        text=text,
    )


_DELAY_PIN = re.compile(r"^~?\{?(EN|CHIP_EN|ENABLE|RUN|N?RST|RESET|N?RESET)\}?$", re.IGNORECASE)


def _rc(design: Design, net: str) -> Calculation | None:
    pins = [n for n in _ic_nodes(design, net) if _DELAY_PIN.match(n.function)]
    if not pins:
        return None
    ups = [r for r in _resistors(design, net) if not is_ground(r.other) and r.value > 0]
    caps = [c for c in _capacitors(design, net) if is_ground(c.other)]
    if len(ups) != 1 or len(caps) != 1:
        return None
    r, c = ups[0], caps[0]
    tau = r.value * c.value
    pin = pins[0]
    return Calculation(
        id="",
        kind="rc_delay",
        ic=pin.ref,
        pins=[pin.pin_ref, *_pins_of(r, c)],
        value=round(tau * 1e3, 3),
        unit="ms",
        expected=None,
        flagged=False,
        text=(
            f"{pin.pin_ref} ({pin.function}) has {r.ref} ({format_ohms(r.value)}) to {r.other} "
            f"and {c.ref} ({format_farads(c.value)}) to ground: an RC delay of "
            f"R×C = {format_seconds(tau)} before it rises to about 63% of {r.other}."
        ),
    )


def _divider(design: Design, net: str) -> Calculation | None:
    ics = _ic_nodes(design, net)
    if not ics:
        return None
    resistors = _resistors(design, net)
    if len(resistors) != 2 or len(design.nets[net]) != len(ics) + 2:
        return None  # anything else on the net (a filter cap, a pull-up) isn't a plain divider
    top = [r for r in resistors if not is_ground(r.other)]
    bottom = [r for r in resistors if is_ground(r.other)]
    if len(top) != 1 or len(bottom) != 1 or top[0].value + bottom[0].value == 0:
        return None
    t, b = top[0], bottom[0]
    ratio = b.value / (t.value + b.value)
    source_v = named_voltage(t.other)
    pins = ", ".join(n.pin_ref + (f"({n.function})" if n.function else "") for n in ics)
    head = (
        f"{t.ref} ({format_ohms(t.value)}) from {t.other} and {b.ref} ({format_ohms(b.value)}) "
        f"to ground divide by {format_ohms(b.value)}/({format_ohms(t.value)} + "
        f"{format_ohms(b.value)}) = {ratio:.3f} into {pins}"
    )
    if source_v is None:
        return Calculation(
            id="",
            kind="divider",
            ic=ics[0].ref,
            pins=[n.pin_ref for n in ics] + _pins_of(t, b),
            value=round(ratio, 3),
            unit="×",
            expected=None,
            flagged=False,
            text=f"{head}: the pin sees {ratio:.3f} × {t.other}.",
        )
    return Calculation(
        id="",
        kind="divider",
        ic=ics[0].ref,
        pins=[n.pin_ref for n in ics] + _pins_of(t, b),
        value=round(source_v * ratio, 3),
        unit="V",
        expected=None,
        flagged=False,
        text=f"{head}: {source_v:g} V × {ratio:.3f} = {source_v * ratio:.2f} V at the pin.",
    )


# Power-input pin names: VDD, VCC, AVDD, IOVDD, VDDIO, USB_VDD, VIN, VI, VBAT, 3V3…
# Not VBUS or VS, which are often sense inputs.
# Not outputs (VOUT, VREG_VOUT) and not reserved or NC pins.
_POWER_IN = re.compile(
    r"^(?:\w+_)?(?:[ADP]|IO)?V(?:DD|CC)\w*$|^(?:VIN?|VBAT|VREG_IN|3V3|V\+)$",
    re.IGNORECASE,
)


def _decoupling(design: Design, ref: str) -> Calculation | None:
    """The capacitors to ground on an IC's supply rails. Flagged when a rail
    has none; a hint, unflagged, when it has fewer than the IC's power pins
    on it."""
    rails: dict[str, list[Node]] = {}
    for net, nodes in design.nets.items():
        if is_ground(net) or len(nodes) < 2:
            continue  # a supply pin alone on its net is unpowered, not undecoupled
        for n in nodes:
            if n.ref == ref and _POWER_IN.match(n.function):
                rails.setdefault(net, []).append(n)
    if not rails:
        return None
    bare: list[str] = []
    few: list[str] = []
    pins: list[str] = []
    caps_used: list[_Two] = []
    for net, nodes in sorted(rails.items()):
        caps = [c for c in _capacitors(design, net) if is_ground(c.other)]
        pins += [n.pin_ref for n in nodes]
        caps_used += caps
        names = ", ".join(f"{n.pin_ref} ({n.function})" for n in nodes)
        if not caps:
            bare.append(f"{net} (on {names}) has no capacitor to ground anywhere on the rail")
        elif len(caps) < len(nodes):
            few.append(
                f"{net} feeds {len(nodes)} power pins ({names}) and has {len(caps)} "
                f"capacitor{'s' if len(caps) > 1 else ''} to ground "
                f"({', '.join(c.ref for c in caps)})"
            )
    if not bare and not few:
        return None
    part = design.parts[ref].value
    if bare:
        text = (
            f"{ref} ({part}) supply: {'; '.join(bare)}. Every IC needs a bypass capacitor "
            "(typically 100 nF) from each supply pin to ground, close to the pin; without one "
            "the rail's noise and the IC's own switching current can make it reset or "
            "misbehave. Check the datasheet for the value."
        )
    else:
        text = (
            f"{ref} ({part}) supply: {'; '.join(few)}. Not flagged: a rail with fewer "
            "capacitors than power pins can be deliberate. Check the datasheet's decoupling "
            "recommendation, which is often one 100 nF per pin."
        )
    return Calculation(
        id="",
        kind="decoupling",
        ic=ref,
        pins=[*pins, *_pins_of(*caps_used)],
        value=float(len(caps_used)),
        unit="capacitors",
        expected=None,
        flagged=bool(bare),
        text=text,
    )


# USB Type-C Specification, sink pull-down Rd = 5.1 kΩ ±10% on each CC pin.
_RD, _RD_TOL = 5100.0, 0.10
_CC_PIN = re.compile(r"^CC([12])$", re.IGNORECASE)


def _cc_pins(design: Design, ref: str) -> dict[str, Node]:
    """{"CC1": node, "CC2": node} for a connector, from its pin names."""
    found: dict[str, Node] = {}
    for nodes in design.nets.values():
        for n in nodes:
            if n.ref == ref and (m := _CC_PIN.match(n.function)):
                found.setdefault(f"CC{m.group(1)}", n)
    return found


def _usb_cc(design: Design, ref: str) -> Calculation | None:
    """A USB-C receptacle's CC pull-downs (Rd). Pins with an IC on their net
    (a PD controller or a CC logic chip) are left to that IC; a CC pin pulled
    up instead of down is a source port (Rp), reported without a flag."""
    cc = _cc_pins(design, ref)
    if set(cc) != {"CC1", "CC2"}:
        return None
    nets = {name: design.net_of(n.pin_ref) or "" for name, n in cc.items()}
    if any(_ic_nodes(design, net) for net in nets.values()):
        return None
    pins = [cc["CC1"].pin_ref, cc["CC2"].pin_ref]
    rule = "each CC pin needs its own 5.1 kΩ ±10% pull-down (Rd) to ground"
    if nets["CC1"] == nets["CC2"]:
        downs = [r for r in _resistors(design, nets["CC1"]) if is_ground(r.other)]
        value = 1 / sum(1 / r.value for r in downs if r.value > 0) if downs else None
        by = f" through {', '.join(r.ref for r in downs)}" if downs else ""
        return Calculation(
            id="",
            kind="usb_cc",
            ic=ref,
            pins=[*pins, *_pins_of(*downs)],
            value=round(value) if value else None,
            unit="Ω",
            expected=_RD,
            flagged=True,
            text=(
                f"USB-C {ref}: CC1 ({pins[0]}) and CC2 ({pins[1]}) are tied together on "
                f"{nets['CC1']} and share one pull-down{by}, but {rule}. With them shared, an "
                "e-marked USB-C to USB-C cable (most 5 A and laptop-charger cables) puts its "
                "~1 kΩ Ra on the same node, so the charger sees Ra instead of a sink and keeps "
                "VBUS off. USB-A to USB-C and plain USB-C cables still work, which hides it."
            ),
        )
    parts: list[str] = []
    problems: list[str] = []
    used: list[_Two] = []
    worst: float | None = _RD
    source = False
    for name in ("CC1", "CC2"):
        net = nets[name]
        resistors = [r for r in _resistors(design, net) if r.value > 0]
        downs = [r for r in resistors if is_ground(r.other)]
        ups = [r for r in resistors if not is_ground(r.other)]
        used += downs + ups
        if ups and not downs:
            listed = ", ".join(f"{r.ref} ({format_ohms(r.value)}) to {r.other}" for r in ups)
            parts.append(f"{name} is pulled up by {listed} (Rp: a source port, not a sink)")
            source = True
            continue
        if not downs:
            problems.append(f"{name} ({cc[name].pin_ref}) has no resistor to ground")
            worst = None
            continue
        value = 1 / sum(1 / r.value for r in downs)
        listed = ", ".join(f"{r.ref} ({format_ohms(r.value)})" for r in downs)
        parts.append(f"{name} has {listed} to ground")
        if abs(value - _RD) > _RD_TOL * _RD:
            problems.append(
                f"{name} sees {format_ohms(round(value))}, outside 4.59–5.61 kΩ"
                + (
                    " (56 kΩ, 22 kΩ and 10 kΩ are Rp pull-up values for a source)"
                    if value > _RD
                    else ""
                )
            )
            if worst is not None:
                worst = value
    flagged = bool(problems)
    text = f"USB-C {ref}:" + (f" {'; '.join(parts)}." if parts else "")
    if flagged:
        text += (
            f" {'; '.join(problems)}, but {rule}. Without it a USB-C to USB-C "
            "charger doesn't see a sink and never turns on VBUS; a USB-A to USB-C cable "
            "still works, which hides the fault."
        )
    elif not source:
        text += " Each CC pin has its own 5.1 kΩ Rd, as a USB-C sink needs."
    return Calculation(
        id="",
        kind="usb_cc",
        ic=ref,
        pins=[*pins, *_pins_of(*used)],
        value=round(worst) if worst is not None else None,
        unit="Ω",
        expected=_RD,
        flagged=flagged,
        text=text,
    )


# Enough to cover a board's regulators, chargers and buses without drowning
# the model's input in LED arithmetic.
_MAX = 16


def lookup(design: Design) -> list[Calculation]:
    """Every calculation the netlist supports, flagged ones first, ids cf1…"""
    calcs: list[Calculation] = []
    fb_nets: set[str] = set()
    for part in sorted(design.parts.values(), key=lambda p: p.ref):
        value = part.value.upper()
        for fb in FEEDBACK_RULES:
            if value.startswith(fb.value_prefix) and (c := _feedback(design, part.ref, fb)):
                calcs.append(c)
                fb_nets.add(design.net_of(f"{part.ref}.{fb.fb_pin}") or "")
        for prog in PROGRAM_RULES:
            if value.startswith(prog.value_prefix) and (c := _program(design, part.ref, prog)):
                calcs.append(c)
                fb_nets.add(design.net_of(f"{part.ref}.{prog.prog_pin}") or "")
        for drop in DROPOUT_RULES:
            if value.startswith(drop.value_prefix) and (c := _dropout(design, part.ref, drop)):
                calcs.append(c)
        if _is_led(design, part.ref) and (c := _led(design, part.ref)):
            calcs.append(c)
        if _head(part.ref) in {"J", "P", "CN", "USB"} and (c := _usb_cc(design, part.ref)):
            calcs.append(c)
        if _head(part.ref) in {"U", "IC"} and (c := _decoupling(design, part.ref)):
            calcs.append(c)
    for net in sorted(design.nets):
        if net in fb_nets:
            continue
        if line := _i2c_line(design, net):
            if c := _i2c(design, net, line):
                calcs.append(c)
        elif c := _rc(design, net) or _divider(design, net):
            calcs.append(c)
    calcs.sort(key=lambda c: not c.flagged)
    return [c.model_copy(update={"id": f"cf{i}"}) for i, c in enumerate(calcs[:_MAX], start=1)]


def format_calculations(calcs: list[Calculation]) -> str:
    """The calculations as the model reads them."""
    if not calcs:
        return (
            "(none: no regulator divider or headroom, charger, LED, I²C, RC, divider, "
            "decoupling or USB-C CC circuit recognised)\n"
        )
    return "\n".join(f"{c.id}{' FLAGGED' if c.flagged else ''}: {c.text}" for c in calcs) + "\n"
