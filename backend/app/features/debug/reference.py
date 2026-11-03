"""Reference designs: what the manufacturer's own documents say a correct
circuit around a few common ICs looks like.

Each note (rd1, …) is a short fact copied from the manufacturer's
datasheet, application note or hardware design guide, with its section.
The model reads them so it stops calling a reference circuit wrong (in the
no-fault eval it flagged the RP2040's 1 kΩ crystal resistor, which is the
Raspberry Pi design) and can cite them. Values calculated with the same
documents' formulas are in ``calc.py``.

Like ``pins.PIN_TABLE`` this is a hand-written MVP source; per-part
datasheet retrieval replaces it (plan 001, "After the hackathon"). Every
entry here was read in the named document, not recalled.
"""

from dataclasses import dataclass

from app.features.debug.design import Design
from app.features.debug.schemas import ReferenceNote

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


def lookup(design: Design) -> list[ReferenceNote]:
    """The reference notes for the parts on this board, once per part type
    (three RP2040s don't need three copies), ids rd1…"""
    notes: list[ReferenceNote] = []
    seen: set[str] = set()
    for part in sorted(design.parts.values(), key=lambda p: p.ref):
        entry = next((r for r in REFERENCES if r.matches(part.value)), None)
        if entry is None or entry.value_prefix in seen:
            continue
        seen.add(entry.value_prefix)
        for text in entry.notes:
            notes.append(
                ReferenceNote(
                    id=f"rd{len(notes) + 1}", part=part.value, source=entry.source, text=text
                )
            )
    return notes


def format_notes(notes: list[ReferenceNote]) -> str:
    """The notes as the model reads them."""
    if not notes:
        return "(none for the parts on this board)\n"
    return "\n".join(f"{n.id} [{n.part}, {n.source}]: {n.text}" for n in notes) + "\n"
