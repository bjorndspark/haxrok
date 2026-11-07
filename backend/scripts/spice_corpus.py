"""Load-check real vendor SPICE models in ngspice, and print a table.

Run via `make spice-corpus` (inside the backend container). Vendor models
aren't committed (their licences don't allow it), so this downloads them:

- TI's parts below, through the app's own model finder (TI's product page,
  then its model ZIPs), kept in ``data/spice-corpus/web/``. Only TI serves
  models to a plain HTTP client (plan 004 step 5);
- every model file put by hand under ``data/spice-corpus/manual/``
  (downloaded in a browser: LTspice's and other vendors' models), one check
  per file, with the files of its folder beside it.

Each model goes through ``spice_convert.check``, as on a real run. A model
that doesn't load names the construct a rewrite would be for.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# The script lives in backend/scripts/ — put the backend dir on sys.path so
# `import app.*` works regardless of the invocation cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.features.debug import spice_convert  # noqa: E402
from app.features.debug.datasheet import HttpFetcher  # noqa: E402
from app.features.debug.design import Part  # noqa: E402
from app.features.debug.project import MODEL_SUFFIXES  # noqa: E402
from app.features.debug.schemas import DatasheetStatus, SpiceModelStatus  # noqa: E402
from app.features.debug.spice import Ngspice  # noqa: E402
from app.features.debug.spice_models import (  # noqa: E402
    ModelFile,
    SpiceModels,
    SpiceModelStore,
    classify,
    pick,
)

# A spread of TI's model kinds: switchers, LDOs, op amps, a comparator, a
# current-sense amp.
TI_PARTS = [
    "TPS63802",
    "TPS62840",
    "TPS54202",
    "TLV755P",
    "TPS7A02",
    "TLV9001",
    "OPA333",
    "TLV3201",
    "INA180",
]

TI_DATASHEET = "https://www.ti.com/lit/ds/symlink/{}.pdf"


async def web(root: Path) -> list[SpiceModelStatus]:
    """Each TI part's model, found (or kept from an earlier run) by the app's
    finder. Each part comes with its TI datasheet URL, as on a real run: its
    ``symlink/<name>.pdf`` names the product page (``TLV755P``, where the
    MPN's stem would give ``TLV755``)."""
    parts = [
        Part(
            ref=mpn,
            value=mpn,
            lib_part="",
            footprint="",
            mpn=mpn,
            manufacturer="Texas Instruments",
        )
        for mpn in TI_PARTS
    ]
    sheets = [
        DatasheetStatus(ref=mpn, value=mpn, status="found", url=TI_DATASHEET.format(mpn.lower()))
        for mpn in TI_PARTS
    ]
    finder = SpiceModels(HttpFetcher(), SpiceModelStore(root / "web"))
    return await finder.find(parts, sheets, {}, "")


def manual(root: Path) -> list[SpiceModelStatus]:
    """One status per model file under ``manual/``, with pick's subcircuit."""
    found = []
    for path in sorted((root / "manual").rglob("*")):
        if not path.is_file() or not path.name.lower().endswith(MODEL_SUFFIXES):
            continue
        text = path.read_bytes()
        picked = pick([ModelFile(path.name, text, str(path))], "", "")
        found.append(
            SpiceModelStatus(
                ref=str(path.relative_to(root / "manual")),
                value=path.stem,
                status=classify(text),
                source="web",
                file=path.name,
                subckt=picked[1] if picked else None,
                reason="" if picked else "no subcircuit or model in it",
                path=str(path),
            )
        )
    return found


def table(statuses: list[SpiceModelStatus]) -> str:
    """A Markdown table, for pasting into the plan."""
    rows = ["| Model | Dialect | Subckt | Loads | Note |", "|---|---|---|---|---|"]
    for s in statuses:
        loads = {True: "yes", False: "**no**", None: "–"}[s.loads]
        note = s.reason.replace("|", "\\|")
        rows.append(f"| {s.ref} | {s.status} | {s.subckt or ''} | {loads} | {note} |")
    return "\n".join(rows)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path("data/spice-corpus"))
    ap.add_argument("--offline", action="store_true", help="only the manual/ files")
    args = ap.parse_args()
    (args.root / "manual").mkdir(parents=True, exist_ok=True)
    hand = manual(args.root)
    statuses = ([] if args.offline else await web(args.root)) + hand
    checked = await spice_convert.check(statuses, {}, Ngspice())
    print(table(checked))
    tried = [s for s in checked if s.loads is not None]
    print(f"\n{sum(bool(s.loads) for s in tried)} of {len(tried)} checked models load.")
    if not hand:
        print(f"No hand-placed models in {args.root / 'manual'}/ yet.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
