# Case study: what PRAT's safety net catches, and what it costs

## Summary

Coding agents running on their own used to fail cards that the same model handled fine in a terminal with a person beside it: they ran out of turns, missed the context, worked on the wrong files. We wanted to know where that stands now, and what PRAT's safety net adds and costs. We ran the same model (Sonnet) on the same 17 bugs in [Rich](https://github.com/Textualize/rich), the Python terminal library, as plain Claude Code with every permission, as Claude Code with a CLAUDE.md, and as Claude Code inside PRAT. The bugs were planted by a seeded mutation sweep, not chosen by hand, and the protocol was published before any agent ran. 473 runs in all, every one reported.

- **An agent on its own now fixes bugs as well as Claude Code does.** Every setup fixed 50 or 51 of 51 runs. PRAT does not make the model a better programmer, and we do not claim it does. That is the point: you give up nothing by putting the agent behind PRAT's rails.
- **The rails caught things the tests did not.** PRAT stopped a 26,500-line line-ending change that the tests and our hidden check both passed. Its reviewer sent back four fixes whose new test never ran the fixed code. Its policy kept the agent inside its worktree six times. Every one of its 1,996 decisions is in a journal you can read.
- **The rails cost time and money.** About 4 to 13 seconds per bug on top of the 42 seconds plain Claude Code takes, and 50 to 80% more model cost (0.13 to 0.16 USD per bug against 0.086). Seeding PRAT's knowledge of a project costs about 6 USD once, and each dream about 2.50 USD.
- **Knowledge helped a little, and learning worked after a fix.** With its knowledge base, PRAT's agent reached the right code in slightly fewer steps. Taught a team rule once by a person, PRAT carried it into every later run, after the benchmark exposed a bug in how PRAT picks knowledge for a card, which we fixed. Claude Code kept the same rule in its CLAUDE.md just as well.

What PRAT is for, then, is not more fixed bugs. It is a branch of your data the agent cannot wreck, a policy that keeps it off your machine, proof and a record of every change, and, since this study, a lesson on every card so the engineer who approves it understands it.

## What the rails caught

From the journals of PRAT's 295 runs. The hidden check measures none of this.

| Event | What happened | Without PRAT |
| --- | --- | --- |
| 26,500-line diff stopped | The builder committed with `core.autocrlf=false` on Windows, rewriting 58 files' line endings. Tests passed. PRAT's diff limit failed the run; the next run handed over 14 lines. | Handed over as is; our hidden check passed it too. |
| 4 fixes sent back by the reviewer | Each new test never reached the fixed code, so it would pass without the fix. The reviewer quoted the line and asked for a test that fails first. | No one reads the test. |
| 7 commands denied | 6 reached outside the card's worktree (`/tmp`, `..`). 1 was over-cautious (a `sed` script with `$p` in single quotes). | Allowed. |
| 1,996 decisions recorded | Every read, write and command, with the decision and the reason. | Nothing recorded unless you build it. |

The reviewer also missed one: an over-fix that changed a second line and so every SVG Rich exports. The tests that would catch it were hidden. Rails reduce risk; they do not remove the need to read the change.

## What it costs

| Setup | Fixed (hidden check) | Total time (s) | Model cost per bug (USD) |
| --- | --- | --- | --- |
| plain Claude Code | 51/51 | 41.5 | 0.086 |
| PRAT, no knowledge | 51/51 | 50.2 | 0.132 |
| PRAT, seeded knowledge | 50/51, and 51/51 after the fix | 50.9 / 45.2 | 0.148 / 0.145 |
| PRAT, after learning and a dream | 51/51, and 51/51 after the fix | 54.3 / 49.0 | 0.155 / 0.148 |

Where a row has two figures, the second is after the knowledge-selection fix. The extra time is PRAT's gates and reviewer; knowledge adds cost too, because it is read on every run. Costs are list-price estimates from the Claude Agent SDK; on a Claude Code login nothing is billed per run. PRAT could run a card up to 3 times when its gates or reviewer sent it back; it needed a second run 4 times in 153 runs, and in all 4 the first run had already passed the hidden check. The extra runs bought better tests and a clean diff, not more fixed bugs. The full breakdown, including the CLAUDE.md setups and steps before the first edit, is in [RESULTS.md](../evals/rich-mutants/RESULTS.md#1-the-main-benchmark-17-bugs-3-runs-each-6-setups).

## Learning from a correction

PRAT's long-term bet is that its knowledge of a project improves over time. Two small experiments tested one piece of it, on one bug, before and after learning, at the same snapshot.

**A rule the gate shows.** The project's lint gate requires a changelog entry. PRAT lists its gates to the builder, which met the rule in all 10 runs. Plain Claude Code met it in 0 of 5; with a CLAUDE.md from `/init`, which found the checker, in 5 of 5. Telling the agent the project's rules up front matters; this does not test learning.

**A rule taught by a person.** A team rule written nowhere in the repository: every bug fix comes with a regression test marked `@pytest.mark.regression` that fails without the fix. Each side did the card once and a person sent it back once with the same comment.

| Setup | Before learning | After learning |
| --- | --- | --- |
| Claude Code with CLAUDE.md | 0/5 | 5/5 |
| PRAT, as released | 0/5 | 0/5 |
| PRAT with the knowledge-selection fix | 0/5 | 5/5 |

PRAT learned the rule, but the entry never reached the builder: notes about Rich's whole 100-module package outranked the note about the one file the card was about. We fixed the scoring, added a test that reproduces the problem, and reran: the rule reached the builder every time. What PRAT adds beyond a CLAUDE.md is curation: entries are checked against the code overnight, merged, credited when they help, retired when wrong, and each records which card taught it. Whether that pays off over months on one codebase is the experiment that matters, and this study does not answer it.

## How the study was run

- **Bugs.** Every comparison in Rich's library code was flipped at its boundary (`<` to `<=` and so on), and every `x + 1` or `x - 1` lost its `1`: 230 candidate bugs. Those that broke between 1 and 6 of Rich's tests qualified (77, in 23 modules). A seeded draw took one per module: 6 for a learning batch, 17 for the test.
- **Cards.** Each bug became a card written from its symptom ("SVG export cuts the terminal one line short"), never naming a file or a line. The tests that catch the bug were taken out of the agent's copy and kept as a hidden check the agent never sees.
- **Setups.** Plain Claude Code; Claude Code with a CLAUDE.md from `/init`; Claude Code with a CLAUDE.md it extended while fixing the 6 learning bugs; PRAT; PRAT with knowledge from its deep scan; PRAT after the 6 learning cards and a dream. Same model, same card text, same project notes, same per-run budget. Each setup ran each bug 3 times.
- **Pre-registration.** The protocol and each addendum were pushed to the public repository before the runs they govern; GitHub records when each push happened.

The protocol, the addenda, every case and the tools are in [evals/rich-mutants](../evals/rich-mutants/).

## Limitations

- **One codebase, one kind of bug.** 17 boundary and off-by-one bugs in one well-known library, which the model has probably seen.
- **Small numbers.** 3 runs per bug per setup, 5 per lesson setup. We claim no statistical significance and report every run.
- **One model and one machine.** Sonnet as builder and reviewer; Windows 11.
- **The rails were tested by accident, not on purpose.** Everything PRAT caught came up in the normal course of fixing bugs. A test built to tempt the agent into dangerous actions is separate work.
- **Pre-registration is in our own repository**, not with a third party.

## Reproducing it

```bash
git clone https://github.com/brimleylabs/prat && cd prat
python -m venv .venv && .venv/bin/python -m pip install -e .
prat eval run evals/rich-mutants/cases/test --runtime plain --label plain --jobs 3
prat eval run evals/rich-mutants/cases/test --runtime claude --label prat --jobs 3
prat eval compare
```

Building the snapshots, the knowledge bases and the CLAUDE.md files, and running every setup, is described in [evals/rich-mutants](../evals/rich-mutants/README.md). Runs made before the protocol are in [evals/exploratory](../evals/exploratory/).
