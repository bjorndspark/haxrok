# Plan 001: MVP scope for the hackathon

| Date | Updated | Status | Tracking | Related |
|---|---|---|---|---|
| 2026-11-01 | 2026-11-01 | In progress | — | `evals/README.md`, `README.md` |

What Häxrök builds for the Meta Global AI Developer Hackathon (2026), what
it leaves out, and the other ideas that were weighed against it. The
constraint is a small scope that must use the Meta model, so the MVP is the
smallest thing that demos the real bug end to end.

## Go/no-go result

`evals/gonogo.py` on a real bug from one of our own boards, model
`meta/muse-spark-1.3-contributor`. The board is private, so its case isn't
in the repo:

| Condition | Rank of the real bug | Time | Tokens (reasoning) |
|---|---|---|---|
| netlist | #1 | 95 s | 16.2k (9.1k) |
| netlist+erc | #1 | 130 s | 22.6k (12.0k) |

By the table in `evals/README.md`, finding it from the netlist alone is a
**go**. The reasoning was real, not keyword matching: it traced why the
board works on some supplies and fails on others through the circuit.

Weaknesses seen, which the MVP has to address:

- **Invented evidence.** It labelled a DMM measurement CONFIRMED, but no
  measurement was in the input.
- **Hallucinated datasheet facts.** It invented a pin requirement for one
  of the ICs and recommended rework based on that.
- **The deeper mechanism was missing.** It never explained why the
  regulator didn't current-limit.
- **Hand-waved gaps.** One failing supply was explained by "source sag"
  instead of being marked as an open question.

This is one case, run once. That's enough to start building, not to claim
accuracy. See the next steps below.

## MVP (in scope)

One feature, `debug`, named the same on both sides
(`backend/app/features/debug/`, `frontend/src/features/debug/`).

1. **Input.** Upload a KiCad `.net` and, optionally, the ERC report, and
   type the bench symptoms. The backend compacts the netlist with the
   `evals/compact_netlist.py` logic (moved into the feature).
2. **One model call.** It uses the `gonogo.py` prompt through
   `app/core/ai/` with `AI_CHAT_MODEL`.
3. **Pin-function lookup.** For each IC on the board it gives the model
   the real pin names and functions, so it stops inventing them. This is
   the fix for the biggest weakness, so it's in the MVP.
4. **Output.** Ranked hypotheses showing the nets and pins involved, how to
   confirm, and the fix. Every claim carries a source label: netlist,
   ERC, datasheet or inferred. Anything that can't be traced to an input
   is shown as inferred.

Done means: a real bug's KiCad project runs from the UI and ranks the
fault #1.

## Stretch (only after the MVP works)

In priority order. If time runs short, cut from the bottom, and keep the
source labels over the simulation.

1. **ngspice check of the top hypothesis**, with a waveform shown in the UI.
2. **Agent loop.** The model asks for a pin lookup or a sim instead of
   getting everything up front.
3. **Pre-fab review mode.** The same pipeline with no symptoms: "review
   before you order". This is the larger market (every board, not only
   broken ones), and the real bug was visible in ERC before fab.
4. **Basic-part substitutes.** When a part is out of stock or is an
   extended JLCPCB part, suggest a basic part that fits the circuit.

## Out of scope

- Importers for non-KiCad tools (Altium, OrCAD). The model only sees the
  compact netlist and ERC text, so keep KiCad parsing behind one import
  step. A later importer is then an addition, not a rewrite.
- A KiCad plugin front end.
- Layout, DRC, assembly or firmware faults. The input is a schematic
  netlist, so the claim is "schematic causes of bench symptoms" only.
- Auth, accounts, history. See `DECISIONS.md`, "No auth".

## Ideas considered instead of Häxrök

| Idea | Need | Fit for the Meta model | Why not now |
|---|---|---|---|
| JLCPCB BOM check: availability, price estimate | Real | Weak: lookups and arithmetic | `kicad-jlcpcb-tools` and JLCPCB's own BOM upload already cover most of it. There's no open JLCPCB parts or pricing API, so it means scraping or a community dump, and assembly pricing isn't a published formula. The one model-shaped part, substitutes, is kept as stretch 4. |
| Datasheet → symbol + footprint | Very high | Good for pin tables, weak for footprint geometry | SnapEDA and Ultra Librarian cover popular parts. One wrong pinout is a dead board, so every output needs a checking step. |
| Pre-fab AI design review | High | Good, same skill | Crowded (Flux and others). Kept as stretch 3 on the same engine instead. |
| AI autorouting | Real | Poor: geometry and optimisation | Funded startups already work on it, and it doesn't fit a hackathon. |
| Visual schematic diff | Real | Barely uses a model | KiRi and similar tools exist, and it doesn't showcase the Meta model. |

### JLCPCB BOM check (parked for later)

Notes so this can be picked up without redoing the research. These are
from memory, not freshly checked, so verify before building.

- **What it would do:** take the KiCad BOM and check JLCPCB/LCSC stock,
  basic vs extended parts and a price estimate for fab plus assembly.
- **What already exists:** `kicad-jlcpcb-tools`, a KiCad plugin that
  matches LCSC part numbers, shows stock and the basic/extended split, and
  generates the BOM and placement files. It's built on `jlcparts`, a
  community-built parts database. JLCPCB's own BOM upload also checks
  parts and quotes a price at order time.
- **Data:** no known open JLCPCB parts or pricing API, so it means the
  `jlcparts` dump or scraping. Assembly pricing (setup fees, per-type
  charges for extended parts, board options) isn't a published formula and
  changes, so a price estimate would drift.
- **Where the model actually helps:** suggesting a substitute that fits
  the circuit when a part is out of stock, or swapping extended parts for
  basic ones to cut the per-type fee. This uses the netlist, so it builds
  on Häxrök's parsing and pin lookup rather than being a separate product.
  That's why it's stretch 4 rather than its own project.
- **Revisit if:** JLCPCB opens a parts or pricing API, or users of the
  debug feature ask for a "can I build this board" check.

Why Häxrök: the core pipeline already works (`gonogo.py`), it's proven on
a real bug from our own board, and starting from bench symptoms sets it
apart from linters that run before fab.

## Product risks

- **Trust.** One confident wrong claim and engineers stop believing it.
  The source labels and pin lookup are the mitigation, which is why both
  are in the MVP.
- **A narrow slice.** Many bring-up faults aren't in the schematic.
  Position it accordingly.
- **Thin evidence.** One case. Fix that before claiming anything in the
  pitch.
- **Market after the hackathon.** KiCad users spend less, so the paying
  segment is likely small hardware teams and consultancies.

## Next steps

1. Rerun `gonogo.py` 2–3 times to check the ranking is stable.
2. Add a second real-bug case, and one fault-free board to check it doesn't
   invent a fault. Public KiCad repos with fix commits are a source of
   labelled cases.
3. Build the MVP, then replace the template's seed chat, voice and image
   features.
