"""The debug prompts. Analysis is two model calls:

1. ``DIAGNOSE``: a free-text diagnosis, worded like ``evals/gonogo.py``'s
   prompt, which the evals show finds the real bug. Asking for the
   structured answer directly made the model follow format rules instead of
   tracing the circuit, and it missed a real wiring bug.
2. ``STRUCTURE``: turn that diagnosis into the structured answer the backend
   checks (``schemas.Analysis``), without diagnosing again.

Both are worded for hobbyists with a multimeter (plan 001, "Who it's for").
Review mode (``REVIEW_DIAGNOSE``, ``REVIEW_STRUCTURE``) is the same pair
before the board is built: no symptoms, look for design mistakes.
``ASK_SIMULATION`` answers a question about one ngspice simulation;
``ASK_RESULT`` answers a follow-up question about a finished run.
``in_language`` makes the user-facing calls (structure, ask) write in the
user's language; the diagnosis stays English, since it is internal reasoning.
"""

from app.features.debug.schemas import (
    AskTurn,
    CheckedAnalysis,
    DesignSummary,
    Language,
    Measurement,
    Mode,
    Simulation,
    Symptom,
)

_INPUT = """\
Input: numbered symptoms (s1, s2, ...), the compact netlist (one line per \
part, then one per net: 'NET: REF.PIN(PIN_NAME) ...'), KiCad's ERC entries \
(erc-1, ...), a pin table copied from datasheets for some ICs, verified \
findings (wf1, ...) the backend computed from the netlist and pin table, \
reference notes (rd1, ...) copied from manufacturers' reference designs and \
datasheets, values the backend calculated from the netlist with datasheet \
formulas (cf1, ...), and sometimes bench measurements the user entered \
(m1, ...)."""

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

A switch's pin numbers don't say which pin is its common: that depends on \
the part, and many slide switches have it on an end pin, not the middle. \
Never assume a middle common. Take the common from how the board wires the \
switch (e.g. the pin that another switch of the same part runs to an IC \
pin); if the wiring can't tell, it is an unverified part fact. A switch \
with one throw wired and the other left open is an ordinary on/off switch.

The pin table is not recalled: it is copied from the datasheets, so its pin \
functions are verified. A fault that follows from the netlist, the ERC and \
the pin table alone (a pin wired against the function the table lists for \
it) is a verified design error. Rank it on how well it explains the \
symptoms; don't demote it for want of a datasheet check.

The verified findings are such faults, found by a deterministic check of the \
netlist against the pin table (e.g. an inductor pin that shares a net with \
anything but its inductor, or a transistor whose symbol numbers its pins \
differently from the part, which puts its nets on the wrong pads). They are \
design errors, not hypotheses: never call one unverified or recalled. Work \
out which symptoms each explains, including only under some conditions \
(input voltage, load, mode), and rank it on that. A finding that explains \
the symptoms well belongs at #1. When a finding names a label as its likely \
cause, the schematic's wires are fine and the label joins the nets: name the \
label and its position, cite the ERC entry, and make the fix moving, renaming \
or deleting that label, not rewiring the pins.

Reference notes are also verified: they are copied from the manufacturer's \
own documents. A circuit that matches a reference note is correct as \
designed: never call it an error or suggest changing it, and use the note \
instead of what you recall. A deviation from a note is worth reporting, with \
its consequence. Calculated values (cf...) are exact arithmetic on the \
netlist's part values; trust them over your own arithmetic. One marked \
FLAGGED is a verified problem in the netlist as drawn (an output named +3V3 \
set to 0.73 V, an LED with no resistor, an I²C line with no pull-up); keep \
any caveat its text gives (a pull-up may sit on a connected module).

Pin warnings (pw...) are not verified: they compare a transistor's symbol \
with the pinout most parts in its package share, not with its own \
datasheet. Report each as a likely error whose first confirm step is \
reading the part's pin diagram; never call one verified."""

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

# Shared by both structure prompts: how a claim is labelled and cited.
_CLAIMS = """\
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
- reference: a fact from a reference note (rd...) or a calculated value \
(cf...). Cite kind reference with its id. Use it for "this matches the \
manufacturer's design" and for calculated voltages and currents.
- measurement: a value the user entered. Cite its id. Symptom text is not a \
measurement id; a claim about what the user reported in the symptoms uses \
evidence none.
- none: reasoning, recalled knowledge, or anything else. Use it freely; it \
is honest.
- Every pin (REF.PIN) and every net named in a claim's text must be cited \
in that claim. Keep claims short so this is easy.
- When a verified finding names a label as its likely cause, add an erc \
claim that copies the label's name, position and sheet file from the \
finding (e.g. "ERC erc-4 reports the SW2 label at (116.84, 48.26) mm on \
power.kicad_sch joined to another net"), citing that ERC id. Give the same \
name and position in the confirm step that inspects it and in the fix, so \
the user knows which of the same-named labels to change.

Keep every caveat the diagnosis gives: a part fact it marks UNVERIFIED stays \
unverified (evidence none, with the datasheet section to check), and a fix \
it makes wait on a check still waits on it."""

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
- severity: null (it is for review mode only).

{_CLAIMS}

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

# --- review mode -----------------------------------------------------------------
#
# The same two calls, before the board exists: no symptoms, so the model
# looks for design mistakes instead of explaining a failure. The risk the
# no-fault eval showed (calling a correct part an error) is worse here, since
# nothing anchors the answer, so the prompt makes "no issues" a valid answer.

_REVIEW_INPUT = """\
Input: optional notes from the designer on what the board is for (s1, s2, \
...; supplies, loads, environment), the compact netlist (one line per part, \
then one per net: 'NET: REF.PIN(PIN_NAME) ...'), KiCad's ERC entries \
(erc-1, ...), a pin table copied from datasheets for some ICs, verified \
findings (wf1, ...) the backend computed from the netlist and pin table, \
reference notes (rd1, ...) copied from manufacturers' reference designs and \
datasheets, and values the backend calculated from the netlist with \
datasheet formulas (cf1, ...)."""

REVIEW_DIAGNOSE = f"""\
You are an expert hardware engineer reviewing a hobbyist's KiCad schematic \
before they order boards. Nothing has been built yet. Your review is the one \
a colleague would give them at a design review.

{_REVIEW_INPUT} Use the part datasheets you know.

Trace the circuit block by block: power entry and protection, regulators \
(enable, feedback, input/output caps, inductor), reset and boot/strapping \
pins, clocks, buses (pull-ups, address conflicts), connectors (USB CC and \
data lines, pin order), unused or floating inputs, back-feed between supplies, \
polarity, and anything ERC flags. For each block, note whether it looks right.

Then list the real issues, at most 5, most severe first. For each: the exact \
nets and pins, what goes wrong on the built board and under which conditions, \
a severity (WILL FAIL: won't work as drawn; MAY FAIL: fails under some \
supply, load or tolerance; IMPROVE: works, but a cheap change makes it more \
robust), how to settle it before ordering (which datasheet section to read, \
or what to check on the first assembled board), and the schematic fix. Mark \
each claim as CONFIRMED (follows directly from the netlist, ERC or pin table) \
or INFERRED.

A design that looks correct is a valid and useful answer: say which blocks \
you checked and that you found no issues. Never pad the list. Style, part \
choice and layout advice are not issues. An ERC warning that is harmless in \
this circuit (e.g. a power pin fed through a connector) is not an issue; say \
why it is harmless.

{_UNVERIFIED}
"""

REVIEW_STRUCTURE = f"""\
You turn a hardware engineer's pre-fab review of a schematic into a \
structured answer for a hobbyist. The review comes first, then the input it \
was made from. The board has not been built.

Do not review again. Keep the review's issues, in its order, with its \
mechanisms, severities and conclusions; don't add, drop, merge or re-rank \
issues. If the review found no issues, return no hypotheses, and put the \
blocks it checked into one open question whose text says the review found \
no issues in them. Restate: plain words, one fact per claim, exact citations \
into the input, checks and fixes in the fields below.

{_REVIEW_INPUT}

Answer: the review's issues as hypotheses (at most 5), most severe first, \
then open questions the input can't answer, then a glossary.

Hypotheses (each is one design issue)
- title: the issue, e.g. "EN pin floats, so the regulator may not start".
- summary: what goes wrong on the built board, and when, in plain words a \
hobbyist understands, 2-4 sentences. No unexplained jargon; put terms in the \
glossary.
- severity: will_fail, may_fail or improve, as the review says.
- explains / does_not_explain: empty lists (there are no symptoms).

{_CLAIMS}

Confirm steps (how to settle the issue before or at first power-up)
- At most 2 per issue. A datasheet check is tool look, power off, purpose \
test_cause, and its instruction names the section. A check on the first \
assembled board says power off or on and where to put the probes.
- readings: each possible outcome and what it establishes. supports / \
rules_out list hypothesis ids.
- setup: empty. practical: true unless it needs equipment a hobbyist lacks.

Fix
- schematic: the change to make before ordering.
- rework: empty (there is no board yet).

Ids: hypotheses h1, h2, ...; confirm steps h1-c1, h1-c2, ...
"""


_LANGUAGE_RULES: dict[Language, str] = {
    "en": "",
    "ja": """

Language: write every sentence the user reads in natural Japanese: titles, \
summaries, claims, instructions, readings, fixes, open questions, glossary \
definitions and answers. Keep these exactly as the input writes them, in \
ASCII: refs and pins (U2, U2.9; write a pin as REF.PIN, never "U2のピン9"), pin \
names, net names, part values, ids (s1, m1, erc-1, rd1, cf1, h1-c1), numbers \
and units (3.3 V, 10 kΩ). A glossary term is the word exactly as it appears \
in your Japanese text (e.g. ERC, バックフィード).""",
}


def in_language(system: str, language: Language) -> str:
    """``system`` with the rule to answer in ``language`` appended
    (nothing for English, the language the prompts are written in)."""
    return system + _LANGUAGE_RULES[language]


def number_symptoms(text: str) -> list[Symptom]:
    """Each non-empty line is one symptom, s1, s2, ... Leading list markers
    are dropped so ids aren't doubled up."""
    lines = [line.strip().lstrip("-*•").strip() for line in text.splitlines()]
    return [Symptom(id=f"s{i}", text=t) for i, t in enumerate((t for t in lines if t), start=1)]


def build_user_message(
    *,
    mode: Mode = "debug",
    symptoms: list[Symptom],
    measurements: list[Measurement],
    netlist: str,
    erc: str,
    pin_table: str,
    findings: str,
    warnings: str = "",
    references: str = "",
    calculations: str = "",
) -> str:
    """The user turn: symptoms (review: the designer's notes), measurements,
    netlist, ERC, pin table, verified findings, unverified pin warnings."""
    parts = []
    if mode == "debug":
        parts.append("## Symptoms\n" + "\n".join(f"{s.id}: {s.text}" for s in symptoms))
    else:
        notes = "\n".join(f"{s.id}: {s.text}" for s in symptoms) or "(none given)"
        parts.append("## Designer's notes on what the board is for\n" + notes)
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
        "## Verified findings (netlist checked against the pin table)\n" + findings,
    ]
    if warnings:
        parts.append(
            "## Pin warnings (UNVERIFIED: a common package pinout, not this part's "
            "datasheet)\n" + warnings
        )
    if references:
        parts.append("## Reference notes (copied from the manufacturers' documents)\n" + references)
    if calculations:
        parts.append("## Calculated from the netlist (verified arithmetic)\n" + calculations)
    return "\n\n".join(parts)


def build_structure_message(*, diagnosis: str, user: str) -> str:
    """The second call's user turn: the diagnosis, then the same input the
    first call saw, so citations can be checked against it."""
    return f"## Diagnosis\n{diagnosis.strip()}\n\n# Input\n\n{user}"


ASK_SIMULATION = """\
You answer a hardware engineer's question about one ngspice simulation of a \
node on their schematic. You get the netlist that was run, the caption, the \
reference lines and a table of samples.

- Answer from those numbers. Quote times and voltages from the table or the \
caption, with units, and say which you used. Interpolate between samples when \
you need to, and say that you did.
- The simulation is an ideal model: an ideal rail switched on at t = 0, one \
resistor, one capacitor. When the answer depends on something it leaves out \
(an IC's threshold or leakage, the real rail's ramp, trace capacitance), say \
so, name the datasheet value to look up, and don't make one up.
- When asked what a different part value would do, work it out from R×C and \
say it's a calculation, not a re-run.
- Short and plain: a few sentences, or a short list. No headings."""


def _samples(sim: Simulation, rows: int = 25) -> str:
    step = max(1, len(sim.t) // rows)
    picked = list(range(0, len(sim.t), step))
    if picked[-1] != len(sim.t) - 1:
        picked.append(len(sim.t) - 1)
    return "\n".join(f"{sim.t[i]:g} {sim.t_unit}\t{sim.v[i]:.3f} V" for i in picked)


def build_ask_message(sim: Simulation, history: list[AskTurn], question: str) -> str:
    """The user turn for a question about ``sim``: the deck, the caption,
    the reference lines, a sample table, the conversation so far, then the
    question."""
    levels = "\n".join(
        f"- {lv.label}" + (" (a limit the waveform shouldn't cross)" if lv.danger else "")
        for lv in sim.levels
    )
    parts = [
        f"## Simulation: {sim.title}",
        "## Netlist that was run\n" + sim.deck.strip(),
        "## Caption\n" + sim.text,
        "## Reference lines\n" + (levels or "(none)"),
        f"## Samples (time, voltage)\n{_samples(sim)}",
    ]
    return _with_question(parts, history, question)


def _with_question(parts: list[str], history: list[AskTurn], question: str) -> str:
    """``parts``, then the conversation so far, then the question."""
    if history:
        turns = "\n\n".join(f"Q: {h.question}\nA: {h.answer}" for h in history)
        parts.append("## Conversation so far\n" + turns)
    parts.append("## Question\n" + question.strip())
    return "\n\n".join(parts)


ASK_RESULT = """\
You answer a hardware engineer's follow-up question about an analysis of \
their KiCad schematic that has already run. You get what the backend parsed \
and checked (verified: ERC, wiring findings, calculations, reference notes; \
unverified: pin warnings) and the findings the analysis reported, each with \
its claims, tests and fixes.

- Answer from that input. Name the finding (h1, ...), part, pin or net you \
mean, and say which entry you used.
- You don't see the full netlist or the datasheets. When the answer depends \
on a connection or part fact the input doesn't show, say so and say what to \
check, and don't make it up.
- A claim marked downgraded didn't hold up against the input; don't repeat it \
as fact.
- When asked to change a fix or a part value, work it out and say it is a \
suggestion to check, not a re-run of the analysis.
- Short and plain: a few sentences, or a short list. No headings."""


def build_result_ask_message(
    summary: DesignSummary, result: CheckedAnalysis, history: list[AskTurn], question: str
) -> str:
    """The user turn for a follow-up about a finished run: what the backend
    checked, every finding with its claims, tests and fixes, the open
    questions, the conversation so far, then the question."""
    review = summary.mode == "review"
    parts = [
        f"## Project: {summary.project} ({summary.mode}; {summary.parts} parts, "
        f"{summary.nets} nets)",
        ("## What the board is for" if review else "## Symptoms")
        + "\n"
        + ("\n".join(f"- {s.id}: {s.text}" for s in summary.symptoms) or "(none given)"),
    ]
    if summary.measurements:
        parts.append(
            "## Measurements\n" + "\n".join(f"- {m.id}: {m.value}" for m in summary.measurements)
        )
    checked = [
        *(f"- {e.id} ERC {e.severity} {e.rule}: {e.message}" for e in summary.erc),
        *(f"- {w.id} (verified): {w.text}" for w in summary.wiring),
        *(f"- {w.id} (unverified): {w.text}" for w in summary.pin_warnings),
        *(f"- {c.id}{' FLAGGED' if c.flagged else ''}: {c.text}" for c in summary.calculations),
        *(f"- {r.id} {r.part} ({r.source}): {r.text}" for r in summary.references),
    ]
    parts.append("## Checked by the backend\n" + ("\n".join(checked) or "(nothing)"))
    findings = []
    for h in result.hypotheses:
        lines = [
            f"### {h.id} (#{h.rank}"
            + (f", {h.severity.replace('_', ' ')}" if h.severity else "")
            + f"): {h.title}",
            h.summary,
        ]
        lines += [
            f"- claim ({c.evidence}{', downgraded' if c.downgraded else ''}): {c.text}"
            for c in h.claims
        ]
        lines += [f"- test {s.id}: {s.instruction}" for s in h.confirm]
        lines += [f"- fix: {f}" for f in h.fix.schematic]
        lines += [f"- rework: {r.text}" for r in h.fix.rework]
        findings.append("\n".join(lines))
    parts.append("## Findings\n" + ("\n\n".join(findings) or "(none: no issues found)"))
    if result.open_questions:
        parts.append(
            "## Open questions\n" + "\n".join(f"- {q.text}" for q in result.open_questions)
        )
    return _with_question(parts, history, question)
