"""The uploaded KiCad project: which files were sent, which sheet is the
root, and whether every sub-sheet the root needs is there.

Pure functions over names and text. Writing the files out and running
``kicad-cli`` is ``kicad.py``'s job.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core.errors import BadRequestError

ALLOWED_SUFFIXES = (".kicad_pro", ".kicad_sch")
# (property "Sheetfile" "power.kicad_sch" ...) — KiCad 6+ schematic format.
_SHEETFILE = re.compile(r'\(property\s+"Sheetfile"\s+"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class Project:
    """Schematic files keyed by their path relative to the project folder,
    plus the root sheet's path."""

    files: dict[str, bytes]
    root: str

    @property
    def name(self) -> str:
        return PurePosixPath(self.root).stem


def normalize_paths(raw: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """Keep only ``.kicad_pro``/``.kicad_sch`` files, and make their paths
    relative to the folder that was dropped.

    A dropped folder arrives as ``board/power.kicad_sch``; a picked single
    file as ``power.kicad_sch``. The common leading folder is removed so
    sub-sheet references (relative to the root sheet) resolve.
    """
    kept: dict[str, bytes] = {}
    for name, data in raw:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise BadRequestError(f"Unsafe file path: {name}")
        if path.suffix in ALLOWED_SUFFIXES:
            kept[str(path)] = data
    if not kept:
        raise BadRequestError("No .kicad_sch file was sent. Drop the KiCad project folder.")
    parts = [PurePosixPath(p).parts for p in kept]
    common = 0
    while all(len(p) > common + 1 for p in parts) and len({p[common] for p in parts}) == 1:
        common += 1
    return {str(PurePosixPath(*PurePosixPath(p).parts[common:])): d for p, d in kept.items()}


def find_root(files: dict[str, bytes]) -> str:
    """The root sheet: the one named after the ``.kicad_pro``, or the only
    sheet when no project file was sent (a flat, one-sheet project)."""
    pros = [p for p in files if p.endswith(".kicad_pro")]
    sheets = [p for p in files if p.endswith(".kicad_sch")]
    if len(pros) > 1:
        raise BadRequestError(
            "More than one .kicad_pro was sent: " + ", ".join(sorted(pros)) + ". Drop one project."
        )
    if pros:
        root = str(PurePosixPath(pros[0]).with_suffix(".kicad_sch"))
        if root not in files:
            raise BadRequestError(f"The root sheet {root} is missing from the dropped folder.")
        return root
    if len(sheets) == 1:
        return sheets[0]
    raise BadRequestError(
        "Several sheets but no .kicad_pro were sent, so the root sheet is unknown. "
        "Drop the whole project folder."
    )


def missing_sheets(files: dict[str, bytes], root: str) -> list[str]:
    """Sub-sheet files the hierarchy references but that weren't sent,
    following references from ``root`` down."""
    missing: list[str] = []
    seen = {root}
    queue = [root]
    while queue:
        sheet = queue.pop()
        base = PurePosixPath(sheet).parent
        text = files[sheet].decode("utf-8", errors="replace")
        for ref in _SHEETFILE.findall(text):
            path = str(base / ref.replace("\\", "/"))
            if path in seen:
                continue
            seen.add(path)
            if path in files:
                queue.append(path)
            else:
                missing.append(path)
    return sorted(missing)


def load_project(raw: list[tuple[str, bytes]]) -> Project:
    """Validate an upload into a ``Project``, naming whatever is missing."""
    files = normalize_paths(raw)
    root = find_root(files)
    if not files[root].lstrip().startswith(b"(kicad_sch"):
        raise BadRequestError(f"{root} isn't a KiCad 6+ schematic.")
    missing = missing_sheets(files, root)
    if missing:
        raise BadRequestError(
            "Sub-sheets referenced by the schematic are missing: " + ", ".join(missing)
        )
    return Project(files=files, root=root)
