"""Run eval cases through the app's own debug prompts, not gonogo.py's.

The app analyzes in two calls (see DECISIONS.md, "Debug analysis is two
model calls"): prompt.DIAGNOSE writes a free-text diagnosis, then
prompt.STRUCTURE turns it into the checked schema. gonogo.py tests neither,
so a green go/no-go says nothing about the app. This sends each case's
symptoms, netlist and ERC (no pin table) through both calls with the
app's own gateway client and AI_CHAT_MODEL.

Runs inside the backend container, which mounts evals/ at /evals:
  make eval-app case=rust-board-fb-swapped

Writes evals/results/<case>/<model>/app-diagnosis.md and app-analysis.json.
Prints where the Auto-check keywords (gonogo.py's rule) first match in the
structured hypotheses; read the answer to judge it. The gateway can't take
much concurrency, so run at most ~3 at once.
"""

import argparse
import asyncio
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
from app.features.debug import prompt  # noqa: E402
from app.features.debug.schemas import Analysis, Claim  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent


def build_input(case_dir: pathlib.Path) -> str:
    """The first call's user message, built as the app builds it."""
    erc = case_dir / "erc.txt"
    return prompt.build_user_message(
        symptoms=prompt.number_symptoms((case_dir / "symptom.md").read_text()),
        measurements=[],
        netlist=(case_dir / "netlist.txt").read_text(),
        erc=erc.read_text() if erc.exists() else "(none)",
        pin_table="(none for these ICs)",
    )


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


async def run(case: str, timeout_s: float) -> None:
    case_dir = ROOT / "cases" / case
    out_dir = ROOT / "results" / case / settings.AI_CHAT_MODEL.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    user = build_input(case_dir)

    start = time.monotonic()
    diagnosis = await complete_text(
        [{"role": "system", "content": prompt.DIAGNOSE}, {"role": "user", "content": user}],
        name="eval_diagnosis",
        timeout_s=timeout_s,
    )
    diagnosed = time.monotonic() - start
    (out_dir / "app-diagnosis.md").write_text(diagnosis)
    analysis = await complete_structured(
        Analysis[Claim],
        [
            {"role": "system", "content": prompt.STRUCTURE},
            {
                "role": "user",
                "content": prompt.build_structure_message(diagnosis=diagnosis, user=user),
            },
        ],
        name="eval_analysis",
        timeout_s=timeout_s,
    )
    (out_dir / "app-analysis.json").write_text(analysis.model_dump_json(indent=1))

    mark = keyword_rank(analysis, auto_check(case_dir))
    titles = " | ".join(f"#{h.rank} {h.title}" for h in analysis.hypotheses)
    print(
        f"{case:22} {mark}  diagnose {diagnosed:4.0f}s  "
        f"total {time.monotonic() - start:4.0f}s  {titles}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="rust-board-fb-swapped")
    ap.add_argument("--timeout", type=float, default=settings.DEBUG_AI_TIMEOUT_S)
    args = ap.parse_args()
    asyncio.run(run(args.case, args.timeout))


if __name__ == "__main__":
    main()
