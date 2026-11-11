"""Run eval cases through the app's own debug prompts, not gonogo.py's.

The app analyzes in two calls (see DECISIONS.md, "Debug analysis is two
model calls"): prompt.DIAGNOSE writes a free-text diagnosis, then
prompt.STRUCTURE turns it into the checked schema. gonogo.py tests neither,
so a green go/no-go says nothing about the app. This sends each case's
symptoms, netlist and ERC, plus the pin table and verified wiring findings
the app derives from the netlist, through both calls with the app's own
gateway client and AI_CHAT_MODEL.

Runs inside the backend container, which mounts evals/ at /evals:
  make eval-app case=rust-board-fb-swapped

Writes evals/results/<case>/<model>/app-diagnosis.md and app-analysis.json.
``--mode review`` sends the case with no symptoms through the review prompts
(pre-fab review) and writes app-review-diagnosis.md / app-review-analysis.json.
Prints where the Auto-check keywords (gonogo.py's rule) first match in the
structured hypotheses; read the answer to judge it. The gateway can't take
much concurrency, so run at most ~3 at once.

``--sim startup[,load_step,dropout]`` also runs the vendor-model
simulations as the app streams them (plan 006 step 1), alongside the
prompts: each IC's datasheet and SPICE model looked up by its value and
the case's ``parts.txt`` (the datasheet URLs a compact netlist drops),
the facts read through the gateway, then ``spice_circuits.simulate``.
Writes app-simulations.json and prints each verdict. Switching models take
minutes per run.
"""

import argparse
import asyncio
import dataclasses
import pathlib
import sys
import time

from gonogo import auto_check  # same directory: /evals

# The backend lives at /app in the container; put it on sys.path so
# `import app.*` works from here.
sys.path.insert(0, "/app")

from app.core.ai.chat import complete_text  # noqa: E402
from app.core.ai.structured import complete_structured  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.features.debug import (  # noqa: E402
    calc,
    mcu,
    pins,
    power,
    prompt,
    reference,
    spice_circuits,
    spice_convert,
)
from app.features.debug.datasheet import Datasheets, DatasheetStore, HttpFetcher  # noqa: E402
from app.features.debug.design import Design, Node, Part  # noqa: E402
from app.features.debug.facts import FactReader, RedisFactsCache  # noqa: E402
from app.features.debug.schemas import (  # noqa: E402
    Analysis,
    Claim,
    DatasheetFacts,
    SimAnalysis,
    Simulation,
)
from app.features.debug.spice import Ngspice  # noqa: E402
from app.features.debug.spice_models import SpiceModels, SpiceModelStore  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent


def parse_compact(text: str) -> Design:
    """Read compact_netlist.py's output back into the app's Design, so the
    pin table and wiring check run on eval cases as they do on uploads.
    Footprints aren't in the compact form; no pin-table entry needs one."""
    parts: dict[str, Part] = {}
    nets: dict[str, list[Node]] = {}
    section = None
    for line in text.splitlines():
        if line.startswith("# "):
            section = line[2:].strip()
        elif not line.strip():
            continue
        elif section == "Components":
            # "REF  VALUE  (lib:part)"; a value may hold single spaces.
            ref, value, lib = (line.split("  ") + ["", ""])[:3]
            parts[ref] = Part(ref, value, lib.strip("()"), "")
        elif section == "Nets":
            name, _, pins_text = line.partition(": ")
            nodes = []
            for token in pins_text.split():
                pin_ref, _, function = token.partition("(")
                ref, _, pin = pin_ref.partition(".")
                nodes.append(Node(ref, pin, function.removesuffix(")")))
            nets[name.removesuffix(":")] = nodes
    return Design(parts, nets)


def build_input(
    case_dir: pathlib.Path, mode: str = "debug"
) -> tuple[str, pins.PinLookup]:
    """The first call's user message, built as the app builds it, and the
    pin lookup it includes. Review mode drops the symptoms."""
    erc = case_dir / "erc.txt"
    netlist = (case_dir / "netlist.txt").read_text()
    design = parse_compact(netlist)
    lookup = pins.lookup(design)
    calcs = calc.lookup(design)
    tree = power.build(design, calcs)
    calcs += mcu.check(design, tree, mcu.rules(design), first_id=len(calcs) + 1)
    symptoms = (
        []
        if mode == "review"
        else prompt.number_symptoms((case_dir / "symptom.md").read_text())
    )
    user = prompt.build_user_message(
        mode="review" if mode == "review" else "debug",
        symptoms=symptoms,
        measurements=[],
        netlist=netlist,
        erc=erc.read_text() if erc.exists() else "(none)",
        pin_table=lookup.text,
        findings=pins.format_findings(lookup.findings),
        warnings=pins.format_warnings(lookup.warnings),
        references=reference.format_notes(reference.lookup(design)),
        calculations=calc.format_calculations(calcs),
        power=power.format_power(tree),
    )
    return user, lookup


def keyword_rank(analysis: Analysis[Claim], groups: list[list[str]] | None) -> str:
    """Rank of the first hypothesis whose title, summary and claims match
    every keyword group; READ for cases without a check, MISS? if none do."""
    if groups is None:
        return "READ "
    for h in analysis.hypotheses:
        text = " ".join([h.title, h.summary, *(c.text for c in h.claims)]).lower()
        if all(any(alt in text for alt in group) for group in groups):
            return f"#{h.rank}   "
    return "MISS?"


def datasheet_urls(case_dir: pathlib.Path) -> dict[str, str]:
    """The case's ``parts.txt``: "REF URL" lines giving the datasheet URL
    the schematic's Datasheet field holds, which the compact netlist drops.
    The datasheet and model finders start from it, as they do on upload.
    No file, no URLs."""
    path = case_dir / "parts.txt"
    if not path.exists():
        return {}
    urls = {}
    for line in path.read_text().splitlines():
        ref, _, url = line.strip().partition(" ")
        if ref and not ref.startswith("#") and url.strip():
            urls[ref] = url.strip()
    return urls


async def _extract(system: str, user: str) -> DatasheetFacts:
    return await complete_structured(
        DatasheetFacts,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        name="eval_datasheet_facts",
        timeout_s=settings.DEBUG_AI_TIMEOUT_S,
    )


async def simulate(
    case_dir: pathlib.Path, analyses: list[SimAnalysis]
) -> list[Simulation]:
    """The case's vendor-model simulations, found, load-checked and run as
    service.prepare and service.stream do for an upload."""
    design = parse_compact((case_dir / "netlist.txt").read_text())
    urls = datasheet_urls(case_dir)
    ics = [design.parts[ic.ref] for ic in pins.lookup(design).ics]
    # A compact netlist keeps no part-number fields; an IC's value is its
    # part number when it has a digit ("TPS63802DLAR", not "~"), and the
    # case's parts.txt gives the schematic's datasheet URL.
    parts = [
        dataclasses.replace(
            p,
            mpn=p.value if any(c.isdigit() for c in p.value) else "",
            datasheet=urls.get(p.ref, ""),
        )
        for p in ics
    ]
    fetcher = HttpFetcher()
    sheets = await Datasheets(
        fetcher, DatasheetStore(pathlib.Path(settings.DATASHEET_DIR))
    ).find(parts)
    finder = SpiceModels(fetcher, SpiceModelStore(pathlib.Path(settings.SPICE_MODEL_DIR)))
    models = await spice_convert.check(await finder.find(parts, sheets, {}, ""), {}, Ngspice())
    reading = await FactReader(_extract, RedisFactsCache()).read(design, sheets)
    runner = Ngspice(timeout_s=settings.NGSPICE_MODEL_TIMEOUT_S)
    return [
        sim
        async for sim in spice_circuits.simulate(models, {}, design, reading, analyses, runner)
    ]


async def run(
    case: str, timeout_s: float, mode: str = "debug", sim: list[SimAnalysis] | None = None
) -> None:
    case_dir = ROOT / "cases" / case
    out_dir = ROOT / "results" / case / settings.AI_CHAT_MODEL.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    sims = asyncio.create_task(simulate(case_dir, sim)) if sim else None
    user, lookup = build_input(case_dir, mode)
    review = mode == "review"
    diagnose_prompt = prompt.REVIEW_DIAGNOSE if review else prompt.DIAGNOSE
    structure_prompt = prompt.REVIEW_STRUCTURE if review else prompt.STRUCTURE
    stem = "app-review" if review else "app"

    start = time.monotonic()
    diagnosis = await complete_text(
        [
            {"role": "system", "content": diagnose_prompt},
            {"role": "user", "content": user},
        ],
        name="eval_diagnosis",
        timeout_s=timeout_s,
    )
    diagnosed = time.monotonic() - start
    (out_dir / f"{stem}-diagnosis.md").write_text(diagnosis)
    analysis = await complete_structured(
        Analysis[Claim],
        [
            {"role": "system", "content": structure_prompt},
            {
                "role": "user",
                "content": prompt.build_structure_message(
                    diagnosis=diagnosis, user=user
                ),
            },
        ],
        name="eval_analysis",
        timeout_s=timeout_s,
    )
    (out_dir / f"{stem}-analysis.json").write_text(analysis.model_dump_json(indent=1))

    mark = keyword_rank(analysis, auto_check(case_dir))
    titles = (
        " | ".join(
            f"#{h.rank}{f' [{h.severity}]' if h.severity else ''} {h.title}"
            for h in analysis.hypotheses
        )
        or "(no issues)"
    )
    findings = ",".join(f.pin for f in lookup.findings) or "-"
    print(
        f"{case:22} {mark}  findings {findings}  diagnose {diagnosed:4.0f}s  "
        f"total {time.monotonic() - start:4.0f}s  {titles}"
    )
    if sims is not None:
        done = await sims
        (out_dir / f"{stem}-simulations.json").write_text(
            "[" + ",\n".join(s.model_dump_json() for s in done) + "]\n"
        )
        for s in done:
            print(f"  {s.id} {s.ref} {s.analysis} {s.verdict}: {s.text}")
        if not done:
            print("  no vendor-model simulation (no IC with a loading model)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="rust-board-fb-swapped")
    ap.add_argument("--timeout", type=float, default=settings.DEBUG_AI_TIMEOUT_S)
    ap.add_argument("--mode", choices=["debug", "review"], default="debug")
    ap.add_argument(
        "--sim",
        default="",
        help="vendor-model simulations to run too, comma-separated: startup,load_step,dropout",
    )
    args = ap.parse_args()
    names = [a.strip() for a in args.sim.split(",") if a.strip()]
    allowed: tuple[SimAnalysis, ...] = ("startup", "load_step", "dropout")
    if bad := [a for a in names if a not in allowed]:
        ap.error(f"--sim: unknown {', '.join(bad)}; pick from {', '.join(allowed)}")
    picked = [a for a in allowed if a in names]
    asyncio.run(run(args.case, args.timeout, args.mode, picked))


if __name__ == "__main__":
    main()
