# Evals

Known schematic bugs, each with the symptoms someone saw on the bench and
the confirmed root cause. They answer one question: does the model find
the real bug?

## Cases

```
cases/<name>/
  symptom.md    what was observed — never the root cause
  netlist.txt   compact netlist (compact_netlist.py output)
  erc.txt       KiCad ERC report, optional
  expected.md   confirmed root cause, fix, and the pass rule
```

| Case | Bug | Source |
|---|---|---|

To add a case from a KiCad project:

```sh
kicad-cli sch export netlist -o board.net board.kicad_sch
kicad-cli sch erc -o erc.rpt board.kicad_sch
python3 -I evals/compact_netlist.py board.net > evals/cases/<name>/netlist.txt
```

Then write `symptom.md` from bench notes only, and `expected.md`.

## Go/no-go

`gonogo.py` sends each case to the model under two conditions:

- **netlist**: symptoms and netlist only. This is the hard test, because
  the model has to reason about the circuit.
- **netlist+erc**: the ERC report is added too. This is realistic, because
  the tool will always have ERC.

```sh
# gateway from AI_BASE_URL / AI_API_KEY (default localhost:8080); load .env first:
#   set -a; . ./.env; set +a
python3 -I evals/gonogo.py                              # meta/muse-spark-1.3-contributor
python3 -I evals/gonogo.py --model meta/muse-spark-1.3
python3 -I evals/gonogo.py --case rust-board-fb-swapped
```

Answers are written to `results/<case>/<model>/<condition>.md`, which is
gitignored. The PASS?/MISS? it prints only checks for keywords, so read
each answer to judge it.

## Reading the result

| Outcome | Meaning |
|---|---|
| Finds it from the netlist alone | The model can reason about circuits, so go ahead |
| Finds it only with ERC | Still go: connecting ERC warnings to symptoms is a real product |
| Misses it even with ERC | This model is too weak; try a larger one before deciding |
