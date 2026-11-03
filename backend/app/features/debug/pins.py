"""Hand-written pin table: what each pin of the demo board's ICs does, so
the model reads real pin functions instead of recalling them.

Each entry was copied from the part's datasheet pin-function table (the
document is named per part). Pin *numbers* key the table, because symbols
name pins differently from datasheets (the ESP32 module's symbol says
``GPIO4/TOUCH4/ADC1_CH3`` where the datasheet says ``IO4``). A part matches
on its value prefix and package, since pin numbers differ per package.

This is the MVP source of datasheet evidence. Per-part datasheet retrieval
replaces it (plan 001, "After the hackathon").
"""

from dataclasses import dataclass

from app.features.debug.design import Design
from app.features.debug.schemas import IcSummary


@dataclass(frozen=True)
class PartPins:
    """One part's pin table. ``pins`` maps pin number to (datasheet name,
    function)."""

    value_prefix: str
    package_hint: str | None
    source: str
    pins: dict[str, tuple[str, str]]

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


def is_ic(ref: str) -> bool:
    """Reference designators KiCad uses for ICs (U1, IC3)."""
    head = ref.rstrip("0123456789")
    return head in {"U", "IC"} and head != ref


def lookup(design: Design) -> PinLookup:
    """Match every IC on the board against the pin table."""
    ics: list[IcSummary] = []
    pin_refs: set[str] = set()
    lines: list[str] = []
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
    return PinLookup(ics=ics, pin_refs=frozenset(pin_refs), text="\n".join(lines) + "\n")


def _ref_key(ref: str) -> tuple[str, int]:
    head = ref.rstrip("0123456789")
    tail = ref[len(head) :]
    return head, int(tail) if tail else 0
