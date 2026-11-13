# Addendum 2: what the sweep found, and the lesson case

Written after the sweep finished and before any case was built or any agent ran.

## The sweep

230 mutants in 62 modules. 117 left every test passing (Rich's suite does not catch them),
25 broke more than 6 tests, 11 made the suite hang (stopped after 180 seconds), and 77
qualified (1 to 6 failing tests). The 77 come from only **23 modules**, so the draw takes one
from each: **23 bugs, 3 short of 26**, as Addendum 1 allowed for. The first 6 are the
learning batch and the other **17** the test batch. Every mutant and its failing tests are in
`sweep/mutants.json`; the draw is in `sweep/drawn.json`.

## The lesson case

Addendum 1 takes the lesson case from a module not used by the draw, and every module with a
qualifying mutant is used. The lesson case is therefore the next qualifying mutant in the same
shuffled walk after the last one drawn, whatever its module. It shares a module with a
test-batch bug, but the lesson experiment runs on its own snapshot (Rich with that one bug), so
the two never meet. `sweep.py --draw-only` repeats the draw from `mutants.json` with this rule.

## Case count

17 test cases, 6 learning cases and 1 lesson case are drawn; with the 5 earlier hand-made
cases (`evals/rich`, `evals/tinyshop`), the repository has 29 cases.
