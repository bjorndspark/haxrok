"""The debug prompts. Analysis is two model calls:

1. ``DIAGNOSE``: a free-text diagnosis, worded like ``evals/gonogo.py``'s
   prompt, which the evals show finds the real bug. Asking for the
   structured answer directly made the model follow format rules instead of
   tracing the circuit, and it missed the demo board's bug.
2. ``STRUCTURE``: turn that diagnosis into the structured answer the backend
   checks (``schemas.Analysis``), without diagnosing again.

Both are worded for hobbyists with a multimeter (plan 001, "Who it's for").
"""

from app.features.debug.schemas import Measurement, Symptom

_INPUT = """\
Input: numbered symptoms (s1, s2, ...), the compact netlist (one line per \
part, then one per net: 'NET: REF.PIN(PIN_NAME) ...'), KiCad's ERC entries \
(erc-1, ...), a pin table copied from datasheets for some ICs, and sometimes \
bench measurements the user entered (m1, ...)."""

# The unverified-facts rule: the model recalls part specs from memory, and in
# the no-fault eval it called a correct part an error and told the user to
# rework it. See DECISIONS.md, "Datasheet facts come from memory until
# retrieval". Its last paragraph exists because the model once applied the
# rule to a fault the pin table proves, and ranked the real bug last.
_UNVERIFIED = """\
Part facts you recall rather than read in the input (thresholds, reference \
voltages, absolute maximums, internal pull-ups, recommended circuits, option \
bits) are UNVERIFIED: say so each time, and name the datasheet section that \
would settle it. Never call part of the design an error, or recommend \
reworking it, on the strength of an unverified fact alone: make checking that \
fact the first confirm step, and say no rework until it is checked. If the \
schematic looks correct for the symptoms, say so plainly and rank causes \
outside the schematic (cable, solder, damaged or wrong part, firmware, \
settings) instead of inventing a design fault.

The pin table is not recalled: it is copied from the datasheets, so its pin \
functions are verified. A fault that follows from the netlist, the ERC and \
the pin table alone (a pin wired against the function the table lists for \
it) is a verified design error. Rank it on how well it explains the \
symptoms; don't demote it for want of a datasheet check."""

DIAGNOSE = f"""\
You are an expert hardware engineer debugging a PCB from its KiCad schematic \
netlist. A hobbyist designed the board, it misbehaves on their bench, and they \
own a multimeter but often no oscilloscope.

{_INPUT} Use the part datasheets you know.

Give your top 3 root-cause hypotheses, ranked. For each: the exact nets and \
pins involved, the mechanism that produces the observed symptoms (which \
symptoms it explains and which it can't), how to confirm it on the bench \
(say power off or on and where to put the probes), and the fix. Mark each \
claim as CONFIRMED (follows directly from the netlist, ERC, pin table or a \
measurement) or INFERRED.

{_UNVERIFIED}
"""

STRUCTURE = f"""\
You turn a hardware engineer's diagnosis of a board into a structured answer \
for a hobbyist with a multimeter. The diagnosis comes first, then the input it \
was made from.

Do not diagnose again. Keep the diagnosis's hypotheses, in its order, with its \
mechanisms and conclusions; don't add, drop, merge or re-rank causes, and don't \
call a cause unlikely that it ranks high. Your job is to restate it: plain \
words, one fact per claim, exact citations into the input, bench steps and \
fixes in the fields below. Where the diagnosis is vague, use the input to make \
it exact (pin numbers, net names), not to change it.

{_INPUT}

Answer: the diagnosis's hypotheses (at most 3), ranked, most likely first, \
then open questions the input can't answer, then a glossary.

Hypotheses
- summary: the mechanism in plain words a hobbyist understands, 2-4 \
sentences. Explain why each symptom happens. No unexplained jargon; put \
terms like ERC, buck-boost, back-feed, strapping pin in the glossary.
- explains / does_not_explain: symptom ids. Be honest about what a \
hypothesis can't explain instead of hand-waving (no vague "source sag"); \
put real gaps in open_questions.

Claims (the evidence behind a hypothesis)
- Each claim states ONE fact and carries an evidence label and citations. \
The backend checks every citation against the input and downgrades the \
claim if any fails, so cite exactly.
- schematic: a fact read from the netlist. Cite pin_on_net (pin as REF.PIN \
and its net, exactly as written) or pin_name (the pin's name as the netlist \
gives it, and use that same name in the text).
- erc: a fact from an ERC entry. Cite its id.
- datasheet: a pin function from the pin table. Cite pin_function with the \
pin. Only pins listed in the pin table can be cited. State only what the pin \
does; put which net it is on in a separate schematic claim.
- measurement: a value the user entered. Cite its id. Symptom text is not a \
measurement id; a claim about what the user reported in the symptoms uses \
evidence none.
- none: reasoning, recalled knowledge, or anything else. Use it freely; it \
is honest.
- Every pin (REF.PIN) and every net named in a claim's text must be cited \
in that claim. Keep claims short so this is easy.

Keep every caveat the diagnosis gives: a part fact it marks UNVERIFIED stays \
unverified (evidence none, with the datasheet section to check), and a fix \
it makes wait on a check still waits on it.

Confirm steps
- Assume a multimeter. Mark tool scope only when nothing else works, and \
give a multimeter alternative in the instruction where one exists.
- Say whether power is off or on, and exactly where to put the probes.
- purpose: test_cause if the step tests the hypothesis; \
check_board_matches_schematic if it only checks the board is built as drawn.
- readings: each possible outcome and what it establishes, e.g. "~0 V on \
EN with power on: the regulator is held off, which confirms h1". Include outcomes \
that establish nothing. supports / rules_out list hypothesis ids.
- A step that changes the board (cutting, lifting a pin, a bodge wire) lists \
the complete intended rewiring in setup, so the circuit under test is \
complete and intended.
- practical is false for things a hobbyist can't do by hand (e.g. probing \
under a QFN); say why in practical_note.

Fix
- schematic: the change for the next revision.
- rework: bodges for the board on the bench, at pin and part level only \
(cut at a pin, lift a pin, bodge-wire pin to pin, swap a part). There is no \
PCB data, so never name traces or vias.

Ids: hypotheses h1, h2, h3; confirm steps h1-c1, h1-c2, ...
"""


def number_symptoms(text: str) -> list[Symptom]:
    """Each non-empty line is one symptom, s1, s2, ... Leading list markers
    are dropped so ids aren't doubled up."""
    lines = [line.strip().lstrip("-*•").strip() for line in text.splitlines()]
    return [Symptom(id=f"s{i}", text=t) for i, t in enumerate((t for t in lines if t), start=1)]


def build_user_message(
    *,
    symptoms: list[Symptom],
    measurements: list[Measurement],
    netlist: str,
    erc: str,
    pin_table: str,
) -> str:
    """The user turn: symptoms, measurements, netlist, ERC, pin table."""
    parts = ["## Symptoms\n" + "\n".join(f"{s.id}: {s.text}" for s in symptoms)]
    if measurements:
        lines = [
            f"{m.id}: {m.value}" + (f"  (for the step: {m.context})" if m.context else "")
            for m in measurements
        ]
        parts.append("## Bench measurements\n" + "\n".join(lines))
    parts += [
        "## Netlist\n" + netlist,
        "## ERC report\n" + erc,
        "## Pin table (from datasheets)\n" + pin_table,
    ]
    return "\n\n".join(parts)


def build_structure_message(*, diagnosis: str, user: str) -> str:
    """The second call's user turn: the diagnosis, then the same input the
    first call saw, so citations can be checked against it."""
    return f"## Diagnosis\n{diagnosis.strip()}\n\n# Input\n\n{user}"
