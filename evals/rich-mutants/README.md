# Rich mutants: the pre-registered benchmark

PRAT and plain Claude Code on bugs planted in [Rich](https://github.com/Textualize/rich) by a
seeded mutation sweep. Read in this order:

1. [PROTOCOL.md](PROTOCOL.md), written before any bug was chosen or any agent ran, and its
   addenda [1](ADDENDUM-1.md), [2](ADDENDUM-2.md) and [3](ADDENDUM-3.md), each written before
   the runs it governs.
2. [RESULTS.md](RESULTS.md): every run, the deviations from the protocol and the raw data.
3. [docs/CASE-STUDY.md](../../docs/CASE-STUDY.md): the write-up.

## What is here

| Path | What |
| --- | --- |
| `sweep/` | every mutant with its failing tests (`mutants.json`) and the draw (`drawn.json`) |
| `cards.json` | the card text for each drawn bug, written from its symptom |
| `cases/learning`, `cases/test` | 6 learning-batch and 17 test-batch cases |
| `cases/lesson`, `cases/lesson2` | the two before-and-after-learning cases |
| `lesson2-feedback.txt` | the person's comment in lesson 2, word for word |
| `results/` | every evaluation's `result.json`, journals and analysis |
| `memory/` | every PRAT knowledge base and CLAUDE.md used, and PRAT's learning reports |
| `tools/` | the scripts below |

## Reproducing it

Each tool's docstring gives its arguments. With a clone of Rich at the protocol's commit:

1. `tools/sweep.py RICH OUT --workers 4`: the sweep and the draw (`--draw-only` repeats the draw
   from `mutants.json`).
2. `tools/build_cases.py build ...`: the cases, verified three ways each, and the snapshots
   (Rich with the bugs present) that both sides learn from. `tools/build_lesson2.py` derives
   lesson 2 and its snapshot from lesson 1.
3. Knowledge for both sides, from a snapshot: `prat project add SNAPSHOT --no-setup` then
   `prat knowledge seed`; and `tools/claude_memory.py init SNAPSHOT OUT` for a CLAUDE.md.
4. Learning: `tools/prat_learning.py` (cards, then a dream) and `tools/claude_memory.py learn`
   (the same cards, CLAUDE.md extended after each); `--person FILE` adds lesson 2's reviewer.
5. The runs: `prat eval run cases/test --runtime plain|claude [--claude-md FILE | --knowledge DIR]
   --label LABEL --jobs 3`, three times per setup.
6. `tools/analyze.py EVALS PREFIX OUT` for the tables; `tools/package_runs.py` to publish runs
   with local folder names replaced.
