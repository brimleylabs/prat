# Addendum 3: a second lesson, taught by a person

Written after the first lesson experiment ran and before any run of the second.

## Why a second lesson experiment

The first lesson experiment (Addendum 1) did not test learning. PRAT's builder is shown the
project's gates and their commands before it starts, so it ran the changelog check itself and
added the entry on its first run in all 10 PRAT runs, before and after the dream. With nothing
going wrong, the dream had nothing to learn from that card, and the "after" runs could not
differ from the "before" runs. (Plain Claude Code, which is not shown the gate, missed the
entry in 5 of 5 runs.) Those results stand and are reported as they are.

To test what the design actually claims, that PRAT learns from a correction so it does not need
the same one twice, the second experiment uses a rule the agent cannot see in advance.

## The case

`cases/lesson2/svg-export-width-regression`, built by `tools/build_lesson2.py` from the first
lesson case: the same bug, the same card, the same Rich commit. Two things differ:

- There is no changelog gate and no changelog checker. The only gate is the test suite.
- The team has a rule written down nowhere in the repository: **every bug fix comes with a
  regression test, marked `@pytest.mark.regression`, that fails without the fix.** The hidden
  check (`check_regression.py`) enforces it: the tests marked `regression` must pass, must fail
  once the bug is put back, and Rich's whole suite must pass with the bug's own tests restored.

## The person

In the learning step a person reviews the card the way a reviewer on the team would. When the
card reaches review they run the hidden check on its branch; if it fails, they send the card
back with this comment, word for word (`lesson2-feedback.txt`), and look again after the next
run, up to 3 runs:

> The fix itself looks right, but this team needs a regression test for every bug fix: add a
> test that fails without your fix and passes with it, and mark it with
> `@pytest.mark.regression` so the regression job picks it up. Bug fixes without one are not
> merged.

## Runs

The snapshot is the lesson snapshot with the changelog checker removed and this case's project
file (`snapshots/lesson2`, built by the same script). It is seeded once, with PRAT's deep scan,
and Claude Code runs `/init` on it once.

- **Before.** 5 runs each: PRAT with the seeded knowledge (`prat-seeded`), plain Claude Code
  (`plain`), Claude Code with the `/init` CLAUDE.md (`plain-md`).
- **Learning.** Each side does the card once in its own copy of the snapshot, with the person
  above. PRAT: the card runs, is sent back while the check fails, is approved when it passes,
  then one dream. Claude Code: a session does the card and is told to add to CLAUDE.md what a
  future developer should know; while the check fails, a new session gets the same comment and
  the same instruction.
- **After.** 5 runs each, on the same case at the same snapshot: PRAT with the knowledge after
  the dream (`prat-learned`), Claude Code with its CLAUDE.md after learning (`plain-md-learned`).

Within a run, PRAT's reviewer and gates work as always; nobody sends the card back during the
measured runs. The measure is the share of runs whose handed-over work passes the hidden check,
then runs per card and time. Everything the dream added or changed about the rule, and the
CLAUDE.md lines about it, are quoted in the results.
