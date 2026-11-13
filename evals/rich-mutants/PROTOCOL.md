# Protocol: PRAT against plain Claude Code on mutated Rich

Written and committed before any bug was chosen or any agent ran. Results are added in a later
commit, beside this file and without changing it.

## Questions

1. Does PRAT hand over correct work more often than plain Claude Code?
2. Does a knowledge base help an agent reach the faulty code sooner?
3. Does knowledge gained from earlier work (PRAT's dream, or Claude Code's own CLAUDE.md)
   help on later, different bugs in the same codebase?

## Codebase

[Rich](https://github.com/Textualize/rich) at commit
`3a109ccdffe5b2d0e30684a304fbd388e99ce57b` (7 November 2026, the head of its default branch
when this protocol was written). Python 3.12 on Windows 11; Rich's own test suite, run with
pytest.

## Choosing the bugs (no hand-picking)

1. **Mutants.** Every module under `rich/` except generated data, platform glue and demos
   (`_unicode_data/`, `_emoji_codes.py`, `_palettes.py`, `_spinners.py`, `_windows*.py`,
   `_win32_console.py`, `jupyter.py`, `__main__.py`, `diagnose.py`, `_export_format.py`,
   `default_styles.py`, `themes.py`, `logging.py`, `traceback.py`, `_inspect.py`). In each,
   every comparison `<`, `<=`, `>`, `>=` is flipped at its boundary (`<` to `<=` and back,
   `>` to `>=` and back), and every `x + 1` or `x - 1` loses its `1`. Each change is one mutant.
2. **Qualifying.** A mutant qualifies when Rich's suite, run against it, has between 1 and 6
   failing tests and no collection error.
3. **Drawing.** The qualifying mutants are shuffled with Python's `random.Random(20261113)`,
   in the order the sweep lists them (module path, then line, then column). Walking the
   shuffled list, a mutant is taken unless its module already gave one, until 10 are taken.
   The first 4 are the **learning batch**; the next 6 are the **test batch**.
4. **Cards.** Each bug becomes a card written from what its failing tests expect: what a user
   did with Rich's public API, what they saw, what they expected. A card never names a file,
   a line or a private function. Every card has the same acceptance criteria:
   the bug is fixed; the existing tests still pass; a test covers the fix.

## Cases

- A test-batch case starts from Rich with **its own bug only**, with no git history, and
  without the Rich tests that catch it (they are taken out of the agent's copy).
- The **hidden check** is Rich's whole test suite with those tests put back. Pass means every
  test passes. The agent never sees the check.
- Each case is verified before use: the visible tests pass with the bug, the hidden check
  fails with it and passes once the original code is restored.

## Knowledge, for both sides

- **Snapshot.** Rich with all 10 bugs present and their catching tests removed. Everything
  that learns about the codebase starts from this snapshot, so no knowledge can describe the
  correct code of a bug.
- **PRAT, seeded.** `prat knowledge seed` (the deep scan) on the snapshot.
- **PRAT, learned.** The seeded project, then the 4 learning-batch bugs run as ordinary PRAT
  cards (builder, gates, reviewer, approval), then one dream cycle (`prat dream now`).
- **Claude Code, CLAUDE.md.** One Claude Code session on the snapshot, asked to write a
  CLAUDE.md for the repository the way `/init` does (commands, architecture, conventions).
- **Claude Code, learned.** That CLAUDE.md, then the 4 learning-batch bugs as plain Claude
  Code sessions, each told at the end to add to CLAUDE.md anything a future developer on this
  codebase should know. The resulting CLAUDE.md is carried forward.
- Every knowledge base and CLAUDE.md used is published with the results, and each is searched
  for any mention of a test-batch bug; any mention is reported.

## Setups (all on the 6 test-batch cases)

| Setup | Agent | Knowledge |
| --- | --- | --- |
| plain | plain Claude Code | none |
| plain-md | plain Claude Code | CLAUDE.md |
| plain-md-learned | plain Claude Code | CLAUDE.md after the learning batch |
| prat | PRAT | none |
| prat-seeded | PRAT | seeded knowledge |
| prat-learned | PRAT | seeded, learning batch, dream |

Plain Claude Code: one session, Claude Code's own system prompt, every permission, no gates,
reviewer or rework; whatever it leaves is handed over. PRAT: its policy, gates (Rich's visible
tests), reviewer, and up to 3 runs per card (rework). Both use the same model (PRAT's default
builder model), the same budget per run (5 USD) and the same card text and project notes.

## Runs and analysis

- Every setup runs every test-batch case **3 times**, at 3 cases at once.
- A run that fails for an infrastructure reason (not the agent's work) is run again, and
  both are reported. No other run is excluded.
- **Primary measure:** the share of runs whose handed-over work passes the hidden check.
- **Secondary:** bad work handed over; time to the first change to a file; files read,
  searches and commands before it; total time per case.
- Reported per setup and per case, as means over the runs, with every run's raw result
  (`result.json` and journals) published.
- No significance testing is claimed at this size; the numbers are reported as measured.
