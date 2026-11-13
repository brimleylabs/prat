# Addendum 4: harder cases, three bugs to a ticket

Written after the results of the main benchmark and the lessons were published, and before
these cases were drawn or any agent ran on them.

## Why

On the main benchmark every setup fixed 50 or 51 of 51 runs: one bug per card, described by its
symptom, was too easy to tell the setups apart. Real tickets are messier. This set asks for more
work per card and gives an agent room to hand over work that is only partly done.

## The pool and the draw

- **Pool.** The qualifying mutants of the sweep (1 to 6 failing tests, no collection error) that
  no case uses yet: 53, in 14 modules.
- **Draw.** The pool is shuffled with Python's `random.Random(20261114)`, in the order
  `mutants.json` lists it. Walking the shuffled list, mutants fill cases of three in turn: a
  mutant joins the case being filled unless that case already has a bug in the same module or a
  bug with a failing test in common, in which case it is skipped. Six cases are filled.
- **Verification.** As before: with the case's three bugs present, the visible tests pass and the
  hidden check fails; with the original code restored, the hidden check passes. A case that does
  not verify is discarded and the walk goes on to fill a replacement.

`tools/build_gnarly.py` does the draw, builds the cases and verifies them.

## The cards

Each case is one ticket: "regressions reported since the last release", listing its three bugs
by symptom, each written from what its failing tests expect, as before (no file, line or
private function named), in the order drawn. The acceptance criteria name each symptom as its own
criterion, then: the existing tests still pass; a test covers each fix. Complexity medium (no
planning pass), up to 3 runs per card for PRAT. The hidden check is Rich's whole suite with every
catching test put back; it passes only if all three bugs are fixed.

## Knowledge

One snapshot holds all 18 bugs, without their catching tests (where two bugs share a module they
are applied together; they never overlap). PRAT seeds its knowledge from it with the deep scan;
Claude Code runs `/init` on it.

## Setups and runs

Plain Claude Code, Claude Code with the `/init` CLAUDE.md, PRAT, and PRAT with seeded knowledge,
on PRAT as it is after the knowledge-selection fix. Each setup runs each case 3 times, 3 cases at
once: 72 runs.

## Measures

- **Primary:** the share of runs whose handed-over work passes the hidden check (all three bugs
  fixed, nothing else broken).
- **Secondary:** bugs fixed out of three (from the failing tests the hidden check names); bad
  work handed over; work PRAT's reviewer or gates sent back and whether it was bad; runs per
  card; time; model cost.
