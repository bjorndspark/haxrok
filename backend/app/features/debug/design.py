"""The parsed schematic: parts and nets from KiCad's netlist, violations from
its ERC, and the compact text form the model reads.

Pure functions over text, no I/O: running ``kicad-cli`` happens elsewhere.
This is the single KiCad import step, so another EDA tool would be another
parser producing the same ``Design``.
"""

import json
import re
from dataclasses import dataclass, field

from app.features.debug.schemas import ErcEntry

# ERC rules that only report library state. The server has no symbol
# libraries, so these fire on every board and say nothing about the design.
LIBRARY_ONLY_RULES = frozenset(
    {"lib_symbol_issues", "lib_symbol_mismatch", "footprint_link_issues"}
)

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')
# "U2.9", "U2.9(L2)", "U2 pin 9", "U2 pin 9 (L2)". The ref must start with letters.
# ASCII word boundaries, so "U2.9が" (Japanese prose) still counts as a mention.
PIN_MENTION = re.compile(r"\b([A-Z]{1,4}\d{1,4})(?:\.|\s+pin\s+)([A-Za-z0-9]{1,5})\b", re.ASCII)


@dataclass(frozen=True)
class Part:
    ref: str
    value: str
    lib_part: str
    footprint: str
    sheet: str = ""  # KiCad's sheet path, e.g. "/Power Management/"; "" if unknown
    # The schematic's datasheet URL; "" when it gives none (KiCad writes "~").
    datasheet: str = ""
    # Part numbers from the symbol's fields (see ``_part_numbers``); "" if absent.
    lcsc: str = ""  # LCSC/JLCPCB part number, "C424093"
    mpn: str = ""  # manufacturer part number
    manufacturer: str = ""
    # The symbol's SPICE model (see ``_sim_fields``); "" if absent.
    sim_library: str = ""  # model file, e.g. "${KIPRJMOD}/spice/TPS63802.lib"
    sim_name: str = ""  # subcircuit or model name in that file


@dataclass(frozen=True)
class Node:
    ref: str
    pin: str
    function: str

    @property
    def pin_ref(self) -> str:
        return f"{self.ref}.{self.pin}"


@dataclass
class Design:
    """Parts and nets of one schematic. ``nets`` keeps KiCad's full names
    (hierarchical ones look like ``/Power/SW1``)."""

    parts: dict[str, Part]
    nets: dict[str, list[Node]]
    _net_of_pin: dict[str, str] = field(init=False, repr=False)
    _pin_name: dict[str, str] = field(init=False, repr=False)
    _by_short_name: dict[str, list[str]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._net_of_pin = {n.pin_ref: name for name, nodes in self.nets.items() for n in nodes}
        self._pin_name = {n.pin_ref: n.function for nodes in self.nets.values() for n in nodes}
        self._by_short_name = {}
        for name in self.nets:
            self._by_short_name.setdefault(short_net_name(name), []).append(name)

    def net_of(self, pin_ref: str) -> str | None:
        """The full net name a pin is on, or None if the pin doesn't exist."""
        return self._net_of_pin.get(pin_ref)

    def pin_name(self, pin_ref: str) -> str:
        """The pin's name from the symbol (KiCad's pinfunction, e.g. "L2"),
        or "" if the pin is unnamed or doesn't exist."""
        return self._pin_name.get(pin_ref, "")

    def resolve_net(self, name: str) -> str | None:
        """Full net name for ``name``, accepting the last path segment when it
        is unambiguous (models often drop the sheet prefix)."""
        name = name.strip()
        if name in self.nets:
            return name
        if "/" + name.lstrip("/") in self.nets:
            return "/" + name.lstrip("/")
        matches = self._by_short_name.get(short_net_name(name), [])
        return matches[0] if len(matches) == 1 else None

    def short_net_names(self) -> dict[str, str]:
        """Unambiguous short name -> full name, for scanning claim text."""
        return {short: full[0] for short, full in self._by_short_name.items() if len(full) == 1}


def short_net_name(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def normalize_pin(text: str) -> str:
    """'U2.9(L2)', ' U2 pin 9 ' -> 'U2.9'. Unrecognised input is returned stripped."""
    text = text.strip()
    m = PIN_MENTION.fullmatch(re.sub(r"\s*\([^)]*\)$", "", text))
    return f"{m.group(1)}.{m.group(2)}" if m else text


# --- KiCad netlist ---------------------------------------------------------------


def _parse_sexpr(text: str) -> list:
    stack: list[list] = [[]]
    for tok in _TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError("unbalanced ')' in netlist")
            node = stack.pop()
            stack[-1].append(node)
        else:
            if tok.startswith('"'):
                tok = tok[1:-1].replace('\\"', '"')
            stack[-1].append(tok)
    if len(stack) != 1 or not stack[0] or not isinstance(stack[0][0], list):
        raise ValueError("not a KiCad s-expression netlist")
    return stack[0][0]


def _children(node: list, name: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def _field(node: list, name: str) -> str:
    found = _children(node, name)
    return found[0][1] if found and len(found[0]) > 1 else ""


def parse_netlist(text: str) -> Design:
    """Parse a KiCad s-expression netlist (``kicad-cli sch export netlist``)."""
    root = _parse_sexpr(text)
    if not root or root[0] != "export":
        raise ValueError("not a KiCad netlist: missing (export ...)")
    parts: dict[str, Part] = {}
    for comps in _children(root, "components"):
        for comp in _children(comps, "comp"):
            lib = _children(comp, "libsource")
            lib_part = f"{_field(lib[0], 'lib')}:{_field(lib[0], 'part')}" if lib else ""
            ref = _field(comp, "ref")
            sheetpath = _children(comp, "sheetpath")
            sheet = _field(sheetpath[0], "names") if sheetpath else ""
            datasheet = _field(comp, "datasheet")
            parts[ref] = Part(
                ref,
                _field(comp, "value"),
                lib_part,
                _field(comp, "footprint"),
                sheet,
                datasheet if datasheet.startswith(("http://", "https://")) else "",
                **_part_numbers(comp),
                **_sim_fields(comp),
            )
    nets: dict[str, list[Node]] = {}
    for net_block in _children(root, "nets"):
        for net in _children(net_block, "net"):
            nets[_field(net, "name")] = [
                Node(_field(n, "ref"), _field(n, "pin"), _field(n, "pinfunction"))
                for n in _children(net, "node")
            ]
    return Design(parts, nets)


# Symbol field names for part numbers, compared lowercased with everything but
# letters and digits dropped: libraries spell them "LCSC Part #", "LCSC Part#",
# "LCSC", "JLCPCB Part #", "MFR. Part #", "MPN", "MP", "MANUFACTURER", "MF"...
_LCSC_KEYS = ("lcsc", "jlcpcb", "jlc")
_MPN_KEYS = frozenset(
    {
        "mpn",
        "mp",
        "mfrpart",
        "mfrpartnumber",
        "mfrpn",
        "mfgpart",
        "mfgpartnumber",
        "manufacturerpart",
        "manufacturerpartnumber",
        "manfpartnumber",
        "partnumber",
    }
)
_MANUFACTURER_KEYS = frozenset({"manufacturer", "manufacturername", "mf", "mfr", "mfg"})
_LCSC_NUMBER = re.compile(r"C\d{2,9}")


def _part_numbers(comp: list) -> dict[str, str]:
    """``lcsc``, ``mpn`` and ``manufacturer`` from a netlist component's
    symbol fields, whatever the library named them; "" when absent. A
    JLCPCB part number is an LCSC one (same catalogue)."""
    found = {"lcsc": "", "mpn": "", "manufacturer": ""}
    for fields in _children(comp, "fields"):
        for f in _children(fields, "field"):
            value = f[2].strip() if len(f) > 2 and isinstance(f[2], str) else ""
            if not value or value == "~":
                continue
            key = re.sub(r"[^a-z0-9]", "", _field(f, "name").lower())
            if key.startswith(_LCSC_KEYS) and _LCSC_NUMBER.fullmatch(value.upper()):
                found["lcsc"] = found["lcsc"] or value.upper()
            elif key in _MPN_KEYS:
                found["mpn"] = found["mpn"] or value
            elif key in _MANUFACTURER_KEYS:
                found["manufacturer"] = found["manufacturer"] or value
    return found


# KiCad 7+ names, then KiCad 6's.
_SIM_KEYS = {
    "Sim.Library": "sim_library",
    "Sim.Name": "sim_name",
    "Spice_Lib_File": "sim_library",
    "Spice_Model": "sim_name",
}


def _sim_fields(comp: list) -> dict[str, str]:
    """``sim_library`` and ``sim_name`` from a netlist component's
    ``Sim.Library``/``Sim.Name`` fields (KiCad 6: ``Spice_Lib_File``/
    ``Spice_Model``); "" when absent."""
    found = {"sim_library": "", "sim_name": ""}
    for fields in _children(comp, "fields"):
        for f in _children(fields, "field"):
            key = _SIM_KEYS.get(_field(f, "name"))
            value = f[2].strip() if len(f) > 2 and isinstance(f[2], str) else ""
            if key and value and value != "~":
                found[key] = found[key] or value
    return found


def compact(design: Design) -> str:
    """The text form the model reads: one line per part, one per net.
    Footprints are included so the model can judge whether rework is
    practical by hand."""
    lines = ["# Components"]
    for p in design.parts.values():
        fp = f"  [{p.footprint}]" if p.footprint else ""
        lines.append(f"{p.ref}  {p.value}  ({p.lib_part}){fp}")
    lines += ["", "# Nets"]
    for name, nodes in design.nets.items():
        pins = " ".join(f"{n.pin_ref}({n.function})" if n.function else n.pin_ref for n in nodes)
        lines.append(f"{name}: {pins}")
    return "\n".join(lines) + "\n"


# --- KiCad schematic labels ---------------------------------------------------------

_LABEL_KINDS = ("label", "global_label", "hierarchical_label")


@dataclass(frozen=True)
class Label:
    """One net label placed on a schematic sheet. ``x``/``y`` are the
    label's position in mm, as KiCad's editor shows it. ``uuid`` is what
    ERC items name it by."""

    name: str
    kind: str  # label (local to its sheet), global_label or hierarchical_label
    file: str  # the .kicad_sch it sits on, relative to the project folder
    x: float
    y: float
    uuid: str


def parse_labels(files: dict[str, bytes]) -> list[Label]:
    """Every net label on every uploaded sheet. The netlist resolves labels
    away (a label merged into a power net leaves no trace there), so this is
    the only place a wrong label can be seen."""
    labels: list[Label] = []
    for path, data in sorted(files.items()):
        if not path.endswith(".kicad_sch"):
            continue
        try:
            root = _parse_sexpr(data.decode("utf-8", errors="replace"))
        except ValueError:
            continue  # kicad-cli opened it, so this is our parser's limit; skip
        for kind in _LABEL_KINDS:
            for node in _children(root, kind):
                at = _children(node, "at")
                if len(node) < 2 or not at or len(at[0]) < 3:
                    continue
                labels.append(
                    Label(
                        name=node[1],
                        kind=kind,
                        file=path,
                        x=float(at[0][1]),
                        y=float(at[0][2]),
                        uuid=_field(node, "uuid"),
                    )
                )
    return labels


# --- KiCad ERC -------------------------------------------------------------------


def parse_erc(text: str) -> list[ErcEntry]:
    """Parse ``kicad-cli sch erc --format json`` output. Library-only rules are
    dropped; the rest are numbered erc-1, erc-2, … in report order."""
    report = json.loads(text)
    entries: list[ErcEntry] = []
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            if v.get("type") in LIBRARY_ONLY_RULES:
                continue
            entries.append(
                ErcEntry(
                    id=f"erc-{len(entries) + 1}",
                    sheet=sheet.get("path", ""),
                    rule=v.get("type", ""),
                    severity=v.get("severity", ""),
                    message=v.get("description", ""),
                    items=[i.get("description", "") for i in v.get("items", [])],
                    uuids=[i.get("uuid", "") for i in v.get("items", [])],
                )
            )
    return entries


def format_erc(entries: list[ErcEntry]) -> str:
    """The ERC as the model reads it, with ids it can cite."""
    lines = []
    for e in entries:
        lines.append(f"{e.id} [{e.rule}] ({e.severity}, sheet {e.sheet}): {e.message}")
        lines += [f"    {item}" for item in e.items]
    return "\n".join(lines) + "\n" if lines else "(no ERC violations)\n"
