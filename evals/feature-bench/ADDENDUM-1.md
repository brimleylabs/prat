# Addendum 1: the plain baseline at the builder's effort

Written on 2026-11-23, after the first 40 Windows runs of [PROTOCOL.md](PROTOCOL.md) and before
any of the runs it adds.

## What we found

In the first runs PRAT as one card finished sooner than plain Claude Code on two of the three
cases. Checking why, we found the two were not set alike: PRAT's builder runs at effort `low` by
default, and the plain baseline ran at the model's default effort, because the baseline's code
never set one. A lower effort means less thinking per step and usually a shorter run. So part
or all of that difference could be the effort setting and not PRAT.

## What this adds

A fifth condition, `plainlow`: the plain baseline exactly as before, with effort `low`. It is
the like-for-like comparison with `single`. The original `plain` runs stay in the results as
they are: they are what a person gets from Claude Code without changing anything.

- Five runs of `plainlow` on each of the three cases, on Windows, one at a time, in rounds.
- Reported by the same rules as the other conditions.
- `prat.evals.plain.run_plain` gains an optional `effort` argument for this; nothing else in
  PRAT changes between the first runs and these.

The macOS repeat of the megastore case keeps the original three conditions.

## How to read the results

Compare `single` with `plainlow` for what PRAT's pipeline costs or saves at the same effort, and
with `plain` for what a person would see against an untuned Claude Code session.
