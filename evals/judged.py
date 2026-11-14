"""Judge a review's issues against a case's ``judged.md`` (plan 012 step 2).

A review raises 10–20 issues; only the planted bug is scored by the
``Auto-check:`` line. Every other issue is either a real flaw of the board
(the planted boards are real open-source boards with their own) or wrong:
a correct part called an error, or a false fact. Each case's
``judged.md`` records those judgements once, and this module matches every
later run's issues against them, since the model words the same issue
differently each time. An LLM judge was rejected: its verdict varies
between runs too.

judged.md holds one entry per issue::

    ## SB1/SB2 short GPIO10/11 to ground
    Verdict: wrong (draft)
    Cites: sb1|sb2
    Match: gnd|ground; gpio10|gpio11|sb1|sb2
    Why: SB1/SB2 are three-pad jumpers; pads 2-3 are bridged by default ...

``Verdict`` is ``real`` or ``wrong``; ``(draft)`` marks a judgement the
agent made that the user hasn't confirmed, counted apart. ``Cites`` and
``Match`` use the Auto-check syntax (groups split by ``;``, every group
must hold; alternatives split by ``|``). ``Cites`` is checked against the
parts, pins and nets the issue's claims cite (``SB1.1`` cites ``sb1.1``,
``sb1`` and its net), each alternative compared whole; ``Match`` against
the issue's title, summary and claim text, as a substring. An entry needs
at least one of the two. The first entry that matches wins, so put the
narrow entries before the broad ones.

Works on plain dicts (an ``Analysis`` hypothesis as ``model_dump()`` or
read back from a run's analysis JSON), so a saved run can be rescored
without the app.
"""

import pathlib
import re
from dataclasses import dataclass
from typing import Any

VERDICTS = ("real", "wrong")
# What an issue is judged as: the case's planted bug, a judged entry's
# verdict, or nothing yet.
BUG, UNJUDGED = "bug", "unjudged"

Groups = list[list[str]]


@dataclass(frozen=True)
class Entry:
    """One judged issue from a case's judged.md."""

    title: str
    verdict: str  # one of VERDICTS
    draft: bool
    cites: Groups | None
    match: Groups | None


@dataclass(frozen=True)
class Judgement:
    """What one issue was judged as, and by which entry (None for a bug or
    an unjudged issue)."""

    verdict: str  # BUG, UNJUDGED or one of VERDICTS
    draft: bool = False
    entry: str | None = None


def groups(spec: str) -> Groups:
    """Keyword groups in the Auto-check syntax, lowercased."""
    return [
        [alt.strip().lower() for alt in group.split("|") if alt.strip()]
        for group in spec.split(";")
        if group.strip()
    ]


def matches(found: set[str] | str, spec: Groups) -> bool:
    """Every group has an alternative in ``found``: a member of a set
    (cites) or a substring of a text (keywords)."""
    return all(any(alt in found for alt in group) for group in spec)


def hypothesis_text(h: dict[str, Any]) -> str:
    """The text a keyword check reads: title, summary and every claim."""
    return " ".join([h["title"], h["summary"], *(c["text"] for c in h["claims"])]).lower()


def hypothesis_cites(h: dict[str, Any]) -> set[str]:
    """Every part, pin and net the hypothesis's claims cite, lowercased:
    a cited pin ``U1.13`` adds ``u1.13`` and ``u1``."""
    out: set[str] = set()
    for claim in h["claims"]:
        for cite in claim["cites"]:
            if pin := cite.get("pin"):
                out.add(pin.lower())
                out.add(pin.split(".", 1)[0].lower())
            if net := cite.get("net"):
                out.add(net.lower())
    return out


_FIELD = re.compile(r"^(Verdict|Cites|Match|Why):\s*(.*)$")


def parse(text: str, source: str = "judged.md") -> list[Entry]:
    """The entries of a judged.md. Raises ValueError naming the line for a
    malformed entry, so a typo can't silently drop a judgement."""
    entries: list[Entry] = []
    title: str | None = None
    fields: dict[str, str] = {}
    start = 0

    def close() -> None:
        if title is None:
            return
        where = f"{source}:{start}"
        m = re.fullmatch(r"(\w+)\s*(\(draft\))?", fields.get("Verdict", ""))
        if not m or m.group(1) not in VERDICTS:
            raise ValueError(f"{where}: Verdict must be real or wrong, optionally (draft)")
        cites = groups(fields["Cites"]) if fields.get("Cites") else None
        match = groups(fields["Match"]) if fields.get("Match") else None
        if not cites and not match:
            raise ValueError(f"{where}: an entry needs Cites or Match")
        entries.append(Entry(title, m.group(1), bool(m.group(2)), cites, match))

    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            close()
            title, fields, start = line[3:].strip(), {}, n
        elif title is not None and (m := _FIELD.match(line)):
            fields[m.group(1)] = m.group(2).strip()
    close()
    return entries


def load(case_dir: pathlib.Path) -> list[Entry]:
    """The case's judged entries; none when it has no judged.md yet."""
    path = case_dir / "judged.md"
    return parse(path.read_text(), str(path)) if path.exists() else []


def judge(h: dict[str, Any], check: Groups | None, entries: list[Entry]) -> Judgement:
    """Judge one issue: the planted bug if the case's Auto-check matches it,
    else the first judged entry that matches, else unjudged."""
    text = hypothesis_text(h)
    if check is not None and matches(text, check):
        return Judgement(BUG)
    cited = hypothesis_cites(h)
    for e in entries:
        if (e.cites is None or matches(cited, e.cites)) and (
            e.match is None or matches(text, e.match)
        ):
            return Judgement(e.verdict, e.draft, e.title)
    return Judgement(UNJUDGED)
