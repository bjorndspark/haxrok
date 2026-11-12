"""Replay a recorded run's evidence check: the structured answer its record
holds, checked again with today's ``evidence`` and ``diagram``, and compared
with the result the run sent. No model call, no Redis.

``make replay run=<id>`` (``backend/scripts/replay.py``) prints the
comparison. After a change to the checker, it shows which recorded claims
it now labels differently, in seconds instead of a new run's minutes.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.features.debug import diagram, record
from app.features.debug.schemas import Analysis, CheckedAnalysis, CheckedClaim, Claim


@dataclass(frozen=True)
class ClaimChange:
    """A claim the checker now labels differently. ``where`` is
    ``<hypothesis id> claim <n>`` (1-based); a side is ``None`` when the
    claim is only on the other one."""

    where: str
    text: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class Replay:
    """The result checked again, and how it differs from the recorded one."""

    result: CheckedAnalysis
    claims: list[ClaimChange]
    problems_added: list[str]
    problems_removed: list[str]
    diagrams_changed: list[str]  # hypothesis ids whose diagram changed

    @property
    def unchanged(self) -> bool:
        return not (
            self.claims or self.problems_added or self.problems_removed or self.diagrams_changed
        )


def replay(lines: Sequence[dict[str, Any]]) -> Replay:
    """Check a record's structured answer again and compare it with the
    result it sent. Raises ``ValueError`` for a record with no context,
    structured answer or result (a run that failed or was cut off)."""
    ctx = _one(lines, "context", lambda d: True)
    answer = _one(lines, "model", lambda d: d.get("name") == "debug_analysis")
    sent = _one(lines, "event", lambda d: d.get("event") == "result")
    recorded = record.evidence_context(ctx)
    result = diagram.checked(
        Analysis[Claim].model_validate(answer["answer"]),
        recorded.evidence,
        recorded.findings,
        recorded.calculations,
    )
    return compare(CheckedAnalysis.model_validate(sent["data"]), result)


def compare(before: CheckedAnalysis, after: CheckedAnalysis) -> Replay:
    """What changed between two checks of the same answer."""
    old, new = _claims(before), _claims(after)
    claims = [
        ClaimChange(
            where=where,
            text=(new.get(where) or old[where]).text,
            before=_label(old.get(where)),
            after=_label(new.get(where)),
        )
        for where in dict.fromkeys([*old, *new])
        if _label(old.get(where)) != _label(new.get(where))
    ]
    old_problems = [f"{p.where}: {p.message}" for p in before.problems]
    new_problems = [f"{p.where}: {p.message}" for p in after.problems]
    diagram_ids = list(dict.fromkeys([*before.diagrams, *after.diagrams]))
    return Replay(
        result=after,
        claims=claims,
        problems_added=[p for p in new_problems if p not in old_problems],
        problems_removed=[p for p in old_problems if p not in new_problems],
        diagrams_changed=[
            h for h in diagram_ids if before.diagrams.get(h) != after.diagrams.get(h)
        ],
    )


def _one(
    lines: Sequence[dict[str, Any]], kind: str, match: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """The last line of ``kind`` whose data ``match`` accepts."""
    found = [line["data"] for line in lines if line["kind"] == kind and match(line["data"])]
    if not found:
        raise ValueError(f"the record has no {kind} line for a replay")
    data: dict[str, Any] = found[-1]
    return data


def _claims(result: CheckedAnalysis) -> dict[str, CheckedClaim]:
    return {
        f"{h.id} claim {i}": c for h in result.hypotheses for i, c in enumerate(h.claims, start=1)
    }


def _label(claim: CheckedClaim | None) -> str | None:
    """``schematic``, or ``none (downgraded from schematic: <reason>)``;
    ``None`` for no claim."""
    if claim is None:
        return None
    if not claim.downgraded:
        return claim.evidence
    return f"{claim.evidence} (downgraded from {claim.claimed_evidence}: {claim.reason})"
