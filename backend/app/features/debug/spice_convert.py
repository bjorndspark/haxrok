"""Makes each found SPICE model run in ngspice, and checks that it loads.

Conversion starts from ngspice's own compatibility modes, set in a
``.spiceinit`` beside the deck: ``ngbehavior=ps`` for a PSpice model (it
already reads ``VALUE={…}``, ``TABLE``, ``IF()``, ``STP()``, ``.FUNC`` and
``PARAMS:``) and ``lt`` for an LTspice one. ``ps`` mode reads only included
files, so a deck always includes the model. A rewrite is added only for a
construct a real vendor model gets wrong (plan 004 steps 5 and 6): in a
PSpice expression, ``LIMIT(x, a, b)``, which ngspice 44 evaluates to 0, and
the XOR operator ``^``, which ngspice reads as a power (TI's XNOR gates
then give the wrong answer). Converted text isn't kept: each run converts
again from the model's ``path`` or the upload.

The load check instantiates the picked subcircuit with each pin to ground
through 1 MΩ and runs ``.op``, with every model file of the model's folder
(or of the upload) beside it. ``SpiceModelStatus.loads`` says whether ngspice
loaded it; an operating point that doesn't converge with the pins grounded
still counts as loaded, with a note in ``reason``. A parameter the model
leaves to its schematic (TI's ``V_out``) is set to 1 and the check run
again, with that need in ``reason``. A model is untrusted text,
so one with a ``.control`` block (which can run shell commands) or an include
outside its own folder is refused, not run. Only the picked file and what it
includes are checked and put beside the deck: vendor ZIPs also hold test
benches that include files ngspice never reads here.
"""

import asyncio
import posixpath
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from app.features.debug.project import MODEL_SUFFIXES
from app.features.debug.schemas import SpiceModelStatus
from app.features.debug.spice import Log, SpiceLoader
from app.features.debug.spice_models import model_names

# The compatibility mode for each dialect; ``ngspice`` needs none.
MODES = {"ngspice": "", "pspice": "ps", "ltspice": "lt"}
# Without this, an operating point that fails gmin and source stepping
# falls back to ngspice's default transient op, which takes minutes on a
# switching regulator's model (TPS63802: over 20 s). Two nanoseconds is
# enough to say whether the model loads.
_OPTRAN = "optran 0 0 0 100p 2n 0"
_PIN_LOAD = "1meg"
_PARALLEL = 3  # load checks at once, per request
_REASON_CHARS = 300
# Parameters the load check sets to 1 when a model needs them (see ``missing_param``).
_MAX_PARAMS = 3
_UNDEFINED = re.compile(r"Undefined parameter \[(\w+)\]")
# A file name the deck's ``.include "<name>"`` can hold as is.
_PLAIN_PATH = re.compile(r'[^"\x00-\x1f\x7f]+')

# What ``pspice_rewrite`` changes: a LIMIT call or the XOR operator.
_REWRITES = re.compile(r"\blimit\s*\(|\^", re.IGNORECASE)
_LIMIT_CALL = re.compile(r"\blimit\s*$", re.IGNORECASE)
_PAIRS = {"(": ")", "{": "}"}

_CONTROL = re.compile(r"^[ \t]*\.(?:control|endc)\b", re.IGNORECASE | re.MULTILINE)
_INCLUDE = re.compile(r"^[ \t]*\.(?:inc\w*|lib)[ \t]+(\S+)", re.IGNORECASE | re.MULTILINE)
# Lines of ngspice's log that start the error worth showing.
_ERROR = re.compile(
    r"error|undefined|unknown|could not|can't|cannot|no such|not found", re.IGNORECASE
)
_BOILERPLATE = re.compile(
    r"^(?:ERROR: fatal error in ngspice|Simulation interrupted due to error|Note:|Warning: Optran)",
    re.IGNORECASE,
)


def subckt_pins(text: bytes, name: str) -> list[str] | None:
    """The pins of ``.subckt <name>``, continuation lines included and
    ``PARAMS:`` or ``key=value`` defaults left out; None if not defined."""
    lines = text.decode("utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        words = line.split()
        if len(words) < 2 or words[0].lower() != ".subckt" or words[1].lower() != name.lower():
            continue
        for more in lines[i + 1 :]:
            if not more.lstrip().startswith("+"):
                break
            words += more.lstrip()[1:].split()
        pins = []
        for w in words[2:]:
            if w.lower().rstrip(":") == "params" or "=" in w:
                break
            pins.append(w)
        return pins
    return None


def reached(files: dict[str, bytes], start: str) -> dict[str, bytes]:
    """``start`` and the files it includes, transitively: what ngspice will
    read. Vendor ZIPs also hold test benches that include files outside
    the folder; those aren't read, so they don't count."""
    out: dict[str, bytes] = {}
    todo = [start]
    while todo:
        name = todo.pop()
        if name in out or name not in files:
            continue
        out[name] = files[name]
        here = posixpath.dirname(name)
        for target in _includes(files[name]):
            todo.append(posixpath.normpath(posixpath.join(here, target.replace("\\", "/"))))
    return out


def unsafe(files: dict[str, bytes]) -> str | None:
    """Why these model files mustn't be run, or None: a ``.control``
    block, or an include path that's absolute or leaves the folder."""
    for name, data in files.items():
        if _CONTROL.search(data.decode("utf-8", errors="replace")):
            return f"{name} has a .control block; not run"
        for path in _includes(data):
            if path.startswith("~") or not _inside(path):
                return f"{name} includes {path}, outside its folder; not run"
    return None


def convert(files: dict[str, bytes], status: str) -> dict[str, bytes]:
    """The model files rewritten for ngspice: a PSpice model's ``LIMIT`` and
    ``^`` (see the module docstring); others are returned as they are."""
    if status != "pspice":
        return files
    return {name: pspice_rewrite(data) for name, data in files.items()}


def pspice_rewrite(data: bytes) -> bytes:
    """One PSpice file with ``LIMIT(x, a, b)`` turned into ``min``/``max``
    and ``a ^ b`` into ``!=`` on truth values, inside each ``{…}``
    expression. A line that needs it is joined with its ``+``
    continuations; a file that needs none comes back unchanged."""
    text = data.decode("latin-1")  # any byte round-trips
    if not _REWRITES.search(text):
        return data
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith("+"):
            j += 1
        group = lines[i:j]
        i = j
        if group[0].lstrip().startswith("*") or not _REWRITES.search("".join(group)):
            out.extend(group)
            continue
        parts = [g.rstrip("\r\n") for g in group]
        body = " ".join([parts[0], *(p.lstrip()[1:] for p in parts[1:])])
        out.append(_braces(body) + group[-1][len(parts[-1]) :])
    return "".join(out).encode("latin-1")


def _braces(line: str) -> str:
    """``line`` with each top-level ``{…}`` expression rewritten."""
    out, i = [], 0
    while (start := line.find("{", i)) != -1 and (end := _close(line, start)) is not None:
        out += [line[i:start], "{", _expr(line[start + 1 : end]), "}"]
        i = end + 1
    return "".join(out) + line[i:]


def _expr(s: str) -> str:
    """One expression rewritten, innermost brackets first."""
    out = ""
    i = 0
    while i < len(s):
        if s[i] not in _PAIRS:
            out += s[i]
            i += 1
            continue
        end = _close(s, i)
        if end is None:  # unbalanced: leave the rest as it is
            return out + s[i:]
        inner = _expr(s[i + 1 : end])
        call = _LIMIT_CALL.search(out) if s[i] == "(" else None
        args = _split(inner, ",")
        if call and len(args) == 3:
            x, a, b = (arg.strip() for arg in args)
            out = out[: call.start()] + f"min(max({x}, min({a}, {b})), max({a}, {b}))"
        else:
            out += s[i] + inner + _PAIRS[s[i]]
        i = end + 1
    return _xor(out)


def _xor(s: str) -> str:
    """``s`` with each top-level ``a ^ b`` as ``((a) != 0) != ((b) != 0)``.
    ``&`` binds tighter than ``^`` and ``|`` looser, as in C."""
    if "^" not in s:
        return s
    args = []
    for arg in _split(s, ","):
        pieces = []
        for piece in _split(arg, "|"):
            terms = _split(piece, "^")
            if len(terms) > 1:
                acc = f"(({terms[0].strip()}) != 0)"
                for term in terms[1:]:
                    acc = f"({acc} != (({term.strip()}) != 0))"
                piece = f" {acc} "
            pieces.append(piece)
        args.append("|".join(pieces))
    return ",".join(args)


def _split(s: str, sep: str) -> list[str]:
    """``s`` split at each ``sep`` outside brackets."""
    parts, depth, start = [], 0, 0
    for i, c in enumerate(s):
        if c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    return [*parts, s[start:]]


def _close(s: str, start: int) -> int | None:
    """The index of the bracket that closes the one at ``start``."""
    depth = 0
    for i in range(start, len(s)):
        if s[i] in "({":
            depth += 1
        elif s[i] in ")}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _includes(data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    return [t.strip("\"'") for t in _INCLUDE.findall(text)]


def spiceinit(status: str) -> str:
    """The ``.spiceinit`` for a model of this dialect."""
    mode = MODES.get(status, "")
    return (f"set ngbehavior={mode}\n" if mode else "") + _OPTRAN + "\n"


def deck(include: str, subckt: str, pins: Sequence[str], params: Sequence[str] = ()) -> str:
    """The load-check deck: the subcircuit with each pin to ground through
    1 MΩ, and an operating point. ``params`` are set to 1 first."""
    nodes = [f"p{i}" for i in range(1, len(pins) + 1)]
    lines = [
        f"* load check: {re.sub(r'[^A-Za-z0-9_.-]', '_', subckt)}",
        f'.include "{include}"',
        *(f".param {p}=1" for p in params),
        f"X1 {' '.join(nodes)} {subckt}",
        *(f"R{i} {n} 0 {_PIN_LOAD}" for i, n in enumerate(nodes, start=1)),
        ".op",
        ".end",
    ]
    return "\n".join(lines) + "\n"


def verdict(log: Log) -> tuple[bool, str]:
    """Whether ngspice loaded the model, and a note for ``reason``: its
    error when it didn't, or why the operating point is missing."""
    started = "Doing analysis" in log.text
    if started and "No. of Data Rows" in log.text:
        return True, ""
    if started:
        if log.code is None:
            return True, "loads; no operating point in the time limit with the pins grounded"
        return True, "loads; no operating point with the pins grounded"
    if log.code is None:
        return False, "ngspice ran out of time loading it"
    return False, _error(log.text) or f"ngspice exited with {log.code}"


def missing_param(log: Log, files: dict[str, bytes]) -> str | None:
    """The parameter ngspice found undefined, when the model uses it as a
    value: one the schematic sets, such as TI's ``V_out`` on TPS7A02. Not
    a name ngspice misread as one (``v`` in LAPLACE's ``V(b)``)."""
    if not (m := _UNDEFINED.search(log.text)):
        return None
    used = re.compile(rf"\b{re.escape(m.group(1))}\b(?!\s*\()", re.IGNORECASE)
    if any(used.search(d.decode("utf-8", errors="replace")) for d in files.values()):
        return m.group(1)
    return None


def _error(text: str) -> str:
    """ngspice's error, from the first line that names one, in a few lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not _BOILERPLATE.match(ln)]
    for i, line in enumerate(lines):
        if _ERROR.search(line):
            return " ".join(lines[i : i + 4])[:_REASON_CHARS]
    return ""


def model_folder(status: SpiceModelStatus, upload: dict[str, bytes]) -> dict[str, bytes] | None:
    """The model files to put beside the deck: the kept folder's (by
    basename) or the upload's (by relative path; one that's absolute or
    climbs out with ``..`` is left out). None if the picked one is gone."""
    if status.source == "upload":
        files = {
            p: d for p, d in upload.items() if p.lower().endswith(MODEL_SUFFIXES) and _inside(p)
        }
        return files if status.file in files else None
    folder = Path(status.path).parent
    try:
        return {
            p.name: p.read_bytes()
            for p in sorted(folder.iterdir())
            if p.is_file() and p.name.lower().endswith(MODEL_SUFFIXES)
        }
    except OSError:
        return None


async def check(
    statuses: Sequence[SpiceModelStatus],
    upload: dict[str, bytes],
    loader: SpiceLoader,
) -> list[SpiceModelStatus]:
    """Each status with ``loads`` set for a found model's subcircuit (a few
    checks at once); the same model twice is checked once. ``upload`` is
    the project's files, for an uploaded model."""
    gate = asyncio.Semaphore(_PARALLEL)
    tasks: dict[tuple[str, str, str], asyncio.Task[tuple[bool | None, str]]] = {}
    for s in statuses:
        if s.status in MODES and s.subckt and (key := _key(s)) not in tasks:
            tasks[key] = asyncio.create_task(_one(s, upload, loader, gate))
    out = []
    for s in statuses:
        if s.status not in MODES or not s.subckt:
            out.append(s)
            continue
        loads, note = await tasks[_key(s)]
        reason = "; ".join(r for r in (s.reason, note) if r)
        out.append(s.model_copy(update={"loads": loads, "reason": reason}))
    return out


def _key(s: SpiceModelStatus) -> tuple[str, str, str]:
    return (s.path or s.file or "", s.subckt or "", s.status)


async def _one(
    s: SpiceModelStatus,
    upload: dict[str, bytes],
    loader: SpiceLoader,
    gate: asyncio.Semaphore,
) -> tuple[bool | None, str]:
    """(loads, note) for one status; loads is None when nothing ran."""
    files = await asyncio.to_thread(model_folder, s, upload)
    name = s.file if s.source == "upload" else Path(s.path).name
    if files is None or name is None or name not in files:
        return None, "model file missing on the server"
    if not _PLAIN_PATH.fullmatch(name):
        return False, f"{name!r} can't be named in a deck; not run"
    assert s.subckt is not None  # checked by the caller
    if s.subckt.lower() in (n.lower() for n in model_names(files[name])[1]):
        return None, "a .model card; only subcircuits are load-checked"
    pins = subckt_pins(files[name], s.subckt)
    if not pins:
        return None, f"{s.subckt} has no pins to connect"
    files = reached(files, name)
    if reason := unsafe(files):
        return False, reason
    models = files
    files = {**convert(files, s.status), ".spiceinit": spiceinit(s.status).encode()}
    params: list[str] = []
    while True:
        async with gate:
            log = await loader.load(deck(name, s.subckt, pins, params), files)
        if log is None:
            return None, ""  # ngspice isn't installed
        loads, note = verdict(log)
        param = None if loads else missing_param(log, models)
        if param is None or param in params or len(params) == _MAX_PARAMS:
            break
        params.append(param)
    if loads and params:
        needs = f"needs {', '.join(params)} set by the schematic (1 here)"
        note = "; ".join(n for n in (needs, note) if n)
    return loads, note


def _inside(path: str) -> bool:
    """Whether a relative path stays inside the folder it's put in."""
    p = PurePosixPath(path.replace("\\", "/"))
    return not p.is_absolute() and ".." not in p.parts and not re.match(r"[A-Za-z]:", path)
