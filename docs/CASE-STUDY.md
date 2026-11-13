# Case study: PRAT and plain Claude Code on 17 bugs in Rich

## Summary

We ran the same model (Sonnet) on the same bugs in [Rich](https://github.com/Textualize/rich),
the Python terminal library, three ways: plain Claude Code, Claude Code with a CLAUDE.md, and
Claude Code inside PRAT. The bugs were planted by a seeded mutation sweep, not chosen by hand;
the protocol was published before any agent ran. 473 runs in all, every one reported.

- **PRAT does not make the model a better programmer, and we do not claim it does.** On the 17
  main bugs, every setup fixed 50 or 51 of 51 runs.
- **PRAT gives you control and a record.** It stopped a 26,500-line line-ending change that the
  tests and our hidden check both passed. Its reviewer sent back four fixes whose new test never
  ran the fixed code. Its policy kept the agent inside its worktree, and every one of its 1,996
  decisions is in a journal you can read.
- **PRAT's knowledge base points the agent at the right code sooner.** With it, PRAT's builder
  made its first edit after 1.5 to 1.7 looks (reads, searches, commands) instead of 2.0 to 2.1.
  A CLAUDE.md written by `/init` made no measurable difference (1.9).
- **PRAT learns from a person's correction.** Taught a team rule once, PRAT's learned knowledge
  carried it into every later run (5 of 5, against 0 of 5 before), after we found and fixed a
  bug in how PRAT picks knowledge for a card. Claude Code given the same correction kept it in
  CLAUDE.md just as well (5 of 5).
- **It costs time.** PRAT's gates and reviewer add about 4 to 13 seconds per bug on top of the
  42 seconds plain Claude Code takes.

## What we are not claiming

PRAT runs Claude Code. The same model writes the same code, so we expected it to fix bugs at
the same rate, and it did. A system that made the model fix more bugs would need a better model
or a better prompt; PRAT's value is elsewhere:

- **Control.** A policy judges every command and file write before it happens. Gates run your
  checks on every change. A reviewer reads the diff against the card. Nothing merges without
  you, and nothing is pushed.
- **Insight.** Every run leaves a journal: each tool call, each policy decision and its reason,
  each gate's output, the reviewer's verdict with evidence per criterion. You can see what the
  agent did, not what it says it did.
- **Memory.** PRAT keeps what it knows about a project as plain files in git, checks them
  against the code while you sleep, and learns from corrections.

The question this study answers is whether those parts work on real code, and how much time they add.

## How the study was run

- **Bugs.** Every comparison in Rich's library code was flipped at its boundary (`<` to `<=`
  and so on), and every `x + 1` or `x - 1` lost its `1`: 230 candidate bugs. Those that broke
  between 1 and 6 of Rich's tests qualified (77, in 23 modules). A seeded draw took one per
  module: 6 for a learning batch, 17 for the test.
- **Cards.** Each bug became a card written from its symptom ("SVG export cuts the terminal one
  line short"), never naming a file or a line. The tests that catch the bug were taken out of
  the agent's copy and kept as a **hidden check** the agent never sees.
- **Setups.** Plain Claude Code; Claude Code with a CLAUDE.md from `/init`; Claude Code with a
  CLAUDE.md it extended while fixing the 6 learning bugs; PRAT; PRAT with knowledge from its deep
  scan; PRAT after the 6 learning cards and a dream. Same model, same card text, same project
  notes. Each setup ran each bug 3 times.
- **Fairness.** Plain Claude Code had every permission and no limits but the same per-run budget
  as each PRAT run. PRAT could run a card up to 3 times when its gates or reviewer sent it back;
  it needed a second run 4 times in 153 main-benchmark runs, and in all 4 the first run had
  already passed the hidden check. Extra runs bought better tests and a clean diff, not more
  fixed bugs.
- **Pre-registration.** The protocol and each addendum were pushed to the public repository
  before the runs they govern; GitHub records when each push happened.

The protocol, the addenda, every case and the tools are in
[evals/rich-mutants](../evals/rich-mutants/). The full results are in
[RESULTS.md](../evals/rich-mutants/RESULTS.md).

## Results: the main benchmark

| Setup | Fixed (hidden check) | Looks before first edit | First edit (s) | Total time (s) |
| --- | --- | --- | --- | --- |
| plain Claude Code | 51/51 | 2.0 | 8.8 | 41.5 |
| Claude Code, `/init` CLAUDE.md | 51/51 | 1.9 | 8.7 | 42.2 |
| Claude Code, CLAUDE.md after learning | 50/51 | 1.9 | 8.7 | 45.0 |
| PRAT, no knowledge | 51/51 | 2.1 | 8.7 | 50.2 |
| PRAT, seeded knowledge | 50/51, and 51/51 after the fix below | 1.6 / 1.7 | 7.6 / 7.5 | 50.9 / 45.2 |
| PRAT, after learning and a dream | 51/51, and 51/51 after the fix | 1.5 / 1.7 | 7.5 / 7.9 | 54.3 / 49.0 |

The one PRAT failure was an over-fix: besides the reported line, the builder changed another
line that alters every SVG Rich exports. The tests that catch it were hidden, and PRAT's
reviewer did not flag the extra change. The one Claude Code failure was an incomplete fix that
guarded one division by zero and missed another.

## Results: what the tooling caught

These are things the hidden check does not measure, from the journals of PRAT's 295 runs:

| Event | What happened | Without PRAT |
| --- | --- | --- |
| 26,500-line diff stopped | The builder committed with `core.autocrlf=false` on Windows, rewriting 58 files' line endings. Tests passed. PRAT's diff limit failed the run; the next run handed over 14 lines. | Handed over as is; our hidden check passed it too. |
| 4 fixes sent back by the reviewer | Each new test never reached the fixed code, so it would pass without the fix. The reviewer quoted the line and asked for a test that fails first. | No one reads the test. |
| 7 commands denied | 6 reached outside the card's worktree (`/tmp`, `..`). 1 was over-cautious (a `sed` script with `$p` in single quotes). | Allowed. |
| 1,996 decisions recorded | Every read, write and command, with the decision and the reason. | Nothing recorded unless you build it. |

## Results: learning from a correction

The design claim is that PRAT learns what it got wrong, so a costly mistake is not repeated.
Two experiments tested it on one bug, before and after learning, at the same snapshot.

**Lesson 1: a rule the gate shows.** The project's lint gate requires a changelog entry. PRAT
lists its gates to the builder, which ran the check itself and met the rule in all 10 runs,
before and after the dream. Plain Claude Code met it in 0 of 5; with a CLAUDE.md from `/init`,
which found the checker, in 5 of 5. This shows the value of telling the agent the project's
rules up front. It does not test learning: nothing went wrong, so there was nothing to learn.

**Lesson 2: a rule taught by a person.** A team rule written nowhere in the repository: every
bug fix comes with a regression test marked `@pytest.mark.regression` that fails without the
fix. Each side did the card once and a person sent it back once with the same comment.

| Setup | Before learning | After learning |
| --- | --- | --- |
| Claude Code with CLAUDE.md | 0/5 | 5/5 |
| PRAT, as released | 0/5 | 0/5 |
| PRAT with the knowledge-selection fix | 0/5 | 5/5 |

PRAT learned the rule correctly; its dream wrote it as a convention entry. But the first "after"
runs failed, and the journals said why: the entry never reached the builder. PRAT picks the
entries most relevant to a card within a budget, and it scored a note about a whole folder as
highly as a note about the one file the card is about. Broad notes about Rich's 100-module
package crowded the rule out. We fixed the scoring (a folder note now counts less the more
files it covers), added a test that reproduces the problem, and ran the PRAT setups again: the
rule reached the builder every time and all 5 runs passed.

Two things to know when comparing the sides. Claude Code's CLAUDE.md also kept this bug's exact
fix, which PRAT's entry does not. And Claude Code was faster here (32 seconds against 61). What
PRAT adds to the learning itself is that its memory is curated: entries are checked against the
code by the dream, merged, credited when they help, retired when wrong, and each records which
card and run taught it.

## Limitations

- **One codebase, one kind of bug.** 17 boundary and off-by-one bugs in one well-known library,
  which the model has probably seen. Harder bugs, feature work and private code may differ.
- **Small numbers.** 3 runs per bug per setup, 5 per lesson setup. We claim no statistical
  significance and report every run.
- **One model and one machine.** Sonnet as builder and reviewer; Windows 11.
- **The hidden check judges the fix, not the test.** That is why the reviewer's four send-backs
  count as "good work sent back" in the raw scores.
- **The reviewer did not get the learned rule** in lesson 2; its smaller knowledge budget filled
  first. The builder did, which was enough here.
- **Pre-registration is in our own repository**, not with a third party.

## Reproducing it

```bash
git clone https://github.com/brimleylabs/prat && cd prat
python -m venv .venv && .venv/bin/python -m pip install -e .
prat eval run evals/rich-mutants/cases/test --runtime plain --label plain --jobs 3
prat eval run evals/rich-mutants/cases/test --runtime claude --label prat --jobs 3
prat eval compare
```

Building the snapshots, the knowledge bases and the CLAUDE.md files, and running every setup,
is described in [evals/rich-mutants](../evals/rich-mutants/README.md). Runs made before the
protocol are in [evals/exploratory](../evals/exploratory/).
