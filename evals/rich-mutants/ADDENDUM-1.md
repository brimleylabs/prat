# Addendum 1 to the protocol

Written and committed before any bug was drawn. The first run of the sweep was stopped part
way through by the machine running low on memory; it drew nothing, and its output was
discarded. Everything in [PROTOCOL.md](PROTOCOL.md) still holds except where this changes it.

## More cases

The project owner asked for 20 to 30 varied cases, so the draw is larger:

- **26 bugs** are drawn by the protocol's rule (the same seed, order and one-per-module rule).
  The first **6** are the learning batch and the next **20** are the test batch. If fewer than
  26 modules have a qualifying mutant, every one is drawn in the same order and the shortfall
  is reported.
- Each of the 6 setups runs each test-batch case **3 times**: 360 runs.
- The sweep runs 4 suites at a time instead of 8, for memory. This changes nothing about
  which mutants qualify or are drawn.

## A lesson to learn: same card, same snapshot, before and after a dream

PRAT's design says it learns from what went wrong, so that a costly mistake is not repeated.
This experiment tests that directly.

- **The case.** The 27th bug in the draw order (the next after the 26) on Rich, with one
  project rule added: Rich's own CONTRIBUTING.md asks every change to be described in
  CHANGELOG.md, and the project's `lint` gate enforces it
  (`tools/check_changelog.py`: a change under `rich/` needs a new entry under
  `## Unreleased`). The project notes do not mention the rule; the builder sees the gate's
  name and command, as PRAT always shows them. The hidden check is Rich's whole test suite and
  the same changelog rule.
- **Before.** PRAT with the project's seeded knowledge runs the case **5 times**.
- **Learning.** The same card runs once as an ordinary PRAT card in the seeded project. A
  failed gate is sent back with its output, as a person would; the card is approved when it
  reaches review. Then one dream cycle runs. If the card passes first time, the dream still
  runs and the result is reported as it is.
- **After.** PRAT with the knowledge after the dream runs the same case, at the same snapshot,
  **5 times**.
- **For reference.** Plain Claude Code (5 runs), and plain Claude Code with a CLAUDE.md written
  by `/init` on the snapshot (5 runs). Neither gets gate feedback, so neither has a lesson to
  learn; the hidden check applies the same rule to them.
- **Measures.** Runs needed to reach review; gate failures; whether the handed-over work
  passes the hidden check; time to review; the lessons the dream wrote, quoted in full.

## Earlier, exploratory runs

The runs made before the protocol (the small shop backend, the first three Rich bugs, and a
first before-and-after-dream comparison) are published as exploratory, separately from the
protocol's results.

## Raw data

`result.json` for every evaluation is published. The journals of every run are published as
compressed archives, with this machine's folder names and user name replaced by placeholders.
Knowledge bases and CLAUDE.md files are published as they were used.
