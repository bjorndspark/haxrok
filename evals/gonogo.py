"""Go/no-go: can the model find a known schematic bug from netlist + symptoms?

Runs each case under two conditions:
  netlist      netlist + symptoms only (hard)
  netlist+erc  also includes the KiCad ERC report (realistic: a tool would have it)

Usage:
  python3 -I evals/gonogo.py [--model meta/muse-spark-1.3-contributor] [--case rust-board-fb-swapped]

Env: AI_BASE_URL (default http://localhost:8080/v1), AI_API_KEY (optional).
Writes each answer to evals/results/<case>/<model>/<condition>.md
"""

import argparse
import json
import os
import pathlib
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent

SYSTEM = (
    "You are an expert hardware engineer debugging a PCB from its schematic netlist. "
    "Each net line is 'NET: REF.PIN(PIN_FUNCTION) ...'. Use the part datasheets you know. "
    "Give your top 3 root-cause hypotheses, ranked. For each: the exact nets/pins "
    "involved, the mechanism that produces the observed symptoms, how to confirm it "
    "(measurement or simulation), and the fix. Mark each claim as CONFIRMED (follows "
    "directly from the netlist/ERC given) or INFERRED."
)


def build_prompt(case_dir, with_erc):
    parts = [
        "## Symptoms\n" + (case_dir / "symptom.md").read_text(),
        "## Netlist\n" + (case_dir / "netlist.txt").read_text(),
    ]
    if with_erc and (case_dir / "erc.txt").exists():
        parts.append("## ERC report\n" + (case_dir / "erc.txt").read_text())
    return "\n\n".join(parts)


def chat(base_url, api_key, model, prompt):
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def auto_check(case_dir):
    """Keyword groups from the 'Auto-check:' line of expected.md, or None.

    Format: groups separated by ';', alternatives within a group by '|'.
    'Auto-check: none' (no-fault cases) or a missing line means no check.
    """
    for line in (case_dir / "expected.md").read_text().splitlines():
        if line.startswith("Auto-check:"):
            spec = line.split(":", 1)[1].strip().lower()
            if spec in ("", "none"):
                return None
            return [[alt.strip() for alt in group.split("|")] for group in spec.split(";")]
    return None


def verdict(answer, groups):
    """Rough keyword check (every group must match); read the answer to be sure."""
    if groups is None:
        return "READ "
    text = answer.lower()
    return "PASS?" if all(any(alt in text for alt in group) for group in groups) else "MISS?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="meta/muse-spark-1.3-contributor")
    ap.add_argument("--case", default="rust-board-fb-swapped")
    args = ap.parse_args()

    base_url = os.environ.get("AI_BASE_URL", "http://localhost:8080/v1")
    api_key = os.environ.get("AI_API_KEY", "")
    case_dir = ROOT / "cases" / args.case
    out_dir = ROOT / "results" / args.case / args.model.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    for condition, with_erc in (("netlist", False), ("netlist+erc", True)):
        start = time.monotonic()
        answer, usage = chat(base_url, api_key, args.model, build_prompt(case_dir, with_erc))
        elapsed = time.monotonic() - start
        (out_dir / f"{condition}.md").write_text(answer)
        mark = verdict(answer, auto_check(case_dir))
        print(f"{args.case:22} {args.model:40} {condition:12} {mark}  {elapsed:5.1f}s  {usage}")


if __name__ == "__main__":
    main()
