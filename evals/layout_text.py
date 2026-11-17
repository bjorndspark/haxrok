"""Print an eval case's ``layout.txt``: the KiCad DRC findings and layout
facts the prompt gets from the case's board (plan 013 step 2).

The facts come from the app's own ``layout.check`` on the case's
``netlist.txt`` and the board, with the unbudgeted power tree (as
``app_prompts.py`` builds it), and the text from ``layout.format_board``,
so the case gets the board section an upload would. Runs inside the
backend container, which mounts evals/ read-only at /evals, so the board
and report go under data/ (/app/data) and the text to stdout:

  docker compose exec -T -e PYTHONPATH=/app backend uv run --no-sync \\
    python /evals/layout_text.py <case> <board.kicad_pcb> <drc.json> \\
    > evals/cases/<case>/layout.txt

``drc.json`` is ``kicad-cli pcb drc --format json --schematic-parity
--severity-all`` on the board, as the app runs it.
"""

import pathlib
import sys

from app_prompts import parse_compact  # same directory: /evals

from app.features.debug import calc, layout, power
from app.features.debug.board import parse
from app.features.debug.design import parse_drc

CASES = pathlib.Path(__file__).resolve().parent / "cases"


def main() -> None:
    case, pcb, drc_json = sys.argv[1:]
    case_dir = CASES / case
    design = parse_compact((case_dir / "netlist.txt").read_text())
    tree = power.build(design, calc.lookup(design))
    facts = layout.check(design, parse(pathlib.Path(pcb).read_text()), tree)
    print(layout.format_board(parse_drc(pathlib.Path(drc_json).read_text()), facts))


if __name__ == "__main__":
    main()
