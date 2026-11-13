# Results: PRAT and plain Claude Code on mutated Rich

The protocol is in [PROTOCOL.md](PROTOCOL.md), with three addenda written before the runs they
govern. This file reports every run. The write-up for readers is
[docs/CASE-STUDY.md](../../docs/CASE-STUDY.md).

All runs: Sonnet (`claude-sonnet-5-5`) as the builder on both sides and as PRAT's reviewer,
Python 3.12 on Windows 11, 13 November 2026.

## 1. The main benchmark: 17 bugs, 3 runs each, 6 setups

| Setup | Runs | Passes hidden check | Bad work handed over | Runs per card | First edit (s) | Looks before it | Total time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| plain Claude Code | 51 | 51/51 | 0 | 1 | 8.8 | 2.0 | 41.5 |
| Claude Code, `/init` CLAUDE.md | 51 | 51/51 | 0 | 1 | 8.7 | 1.9 | 42.2 |
| Claude Code, CLAUDE.md after learning | 51 | 50/51 | 1 | 1 | 8.7 | 1.9 | 45.0 |
| PRAT, no knowledge | 51 | 51/51 | 0 | 1.04 | 8.7 | 2.1 | 50.2 |
| PRAT, seeded knowledge | 51 | 50/51 | 1 | 1 | 7.6 | 1.6 | 50.9 |
| PRAT, after learning and a dream | 51 | 51/51 | 0 | 1.04 | 7.5 | 1.5 | 54.3 |
| PRAT, seeded, selection fix (section 4) | 51 | 51/51 | 0 | 1.02 | 7.5 | 1.7 | 45.2 |
| PRAT, learned, selection fix (section 4) | 51 | 51/51 | 0 | 1 | 7.9 | 1.7 | 49.0 |

"Looks" are file reads, searches and commands before the first change to a file. "Total time"
for PRAT includes its gates and its reviewer.

Per case (passes, then seconds to the first edit), from `results/bench/analysis.json`:

| Case | plain | plain-md | plain-md-learned | prat | prat-seeded | prat-learned |
| --- | --- | --- | --- | --- | --- | --- |
| ansi-charset-escape | 3/3, 13.2 | 3/3, 14.5 | 3/3, 12.5 | 3/3, 10.7 | 3/3, 10.2 | 3/3, 8.9 |
| bar-zero-size | 3/3, 10.4 | 3/3, 7.9 | 3/3, 9.1 | 3/3, 9.9 | 3/3, 9.2 | 3/3, 9.1 |
| color-parse-255 | 3/3, 6.4 | 3/3, 6.4 | 3/3, 5.8 | 3/3, 7.2 | 3/3, 5.4 | 3/3, 5.4 |
| columns-exact-width | 3/3, 9.7 | 3/3, 9.1 | **2/3**, 9.8 | 3/3, 8.5 | 3/3, 8.8 | 3/3, 8.3 |
| filesize-thousand-bytes | 3/3, 10.8 | 3/3, 8.5 | 3/3, 8.1 | 3/3, 6.5 | 3/3, 6.6 | 3/3, 6.8 |
| json-highlighter-string | 3/3, 6.8 | 3/3, 6.0 | 3/3, 6.2 | 3/3, 6.3 | 3/3, 5.5 | 3/3, 5.9 |
| live-exact-height | 3/3, 7.5 | 3/3, 8.8 | 3/3, 8.3 | 3/3, 7.9 | 3/3, 7.9 | 3/3, 7.4 |
| markdown-list-numbers | 3/3, 8.8 | 3/3, 9.6 | 3/3, 10.1 | 3/3, 12.3 | 3/3, 7.3 | 3/3, 9.3 |
| progress-bar-complete-style | 3/3, 5.6 | 3/3, 5.8 | 3/3, 6.0 | 3/3, 5.7 | 3/3, 5.6 | 3/3, 5.8 |
| progress-finish-at-total | 3/3, 9.4 | 3/3, 9.3 | 3/3, 8.9 | 3/3, 9.2 | 3/3, 9.4 | 3/3, 9.3 |
| ratio-zero-last | 3/3, 7.0 | 3/3, 6.8 | 3/3, 8.5 | 3/3, 6.7 | 3/3, 6.4 | 3/3, 6.4 |
| rule-right-title | 3/3, 7.7 | 3/3, 8.0 | 3/3, 7.4 | 3/3, 7.0 | 3/3, 5.7 | 3/3, 6.1 |
| segment-split-wide-char | 3/3, 10.5 | 3/3, 10.1 | 3/3, 10.8 | 3/3, 17.2 | 3/3, 8.0 | 3/3, 8.1 |
| svg-export-height | 3/3, 9.4 | 3/3, 8.2 | 3/3, 8.1 | 3/3, 9.7 | **2/3**, 10.9 | 3/3, 9.9 |
| table-footer-extra-line | 3/3, 10.9 | 3/3, 11.3 | 3/3, 11.1 | 3/3, 9.1 | 3/3, 9.0 | 3/3, 8.2 |
| text-stylize-end-zero | 3/3, 6.2 | 3/3, 7.7 | 3/3, 7.3 | 3/3, 5.7 | 3/3, 6.0 | 3/3, 6.1 |
| wrap-exact-word | 3/3, 9.0 | 3/3, 9.4 | 3/3, 10.0 | 3/3, 9.0 | 3/3, 6.8 | 3/3, 6.7 |

**The two failures.**

- `svg-export-height`, PRAT seeded, run 1. The builder fixed the reported line and also changed
  `range(y)` to `range(y + 1)` a few lines above, which adds a clip line to every SVG. The
  tests that would catch it are among the hidden ones; the visible tests passed and the
  reviewer did not flag the extra change. A miss by PRAT's reviewer.
- `columns-exact-width`, Claude Code with the learned CLAUDE.md, run 1. The fix guarded one
  modulo by zero in `rich/columns.py` and left a second one on another path.

**Where PRAT sent work back or stopped it** (all PRAT runs, sections 1 to 4: 295 runs, 300
builder sessions):

| What | Count | What it was |
| --- | --- | --- |
| Reviewer sent the work back | 4 | Each time the builder's new test never reached the fixed code, so it would pass without the fix. The card asks for "a test covers the fix". The rework added a test that does; the hidden check does not judge tests, so it scores these as good work sent back. |
| Gate stopped the run | 1 | The builder committed with `git -c core.autocrlf=false add -A`, turning 58 files to Windows line endings: a 26,500-line diff. Rich's tests and the hidden check both passed it. PRAT's diff limit (5,000 lines) failed the run; the next run handed over a 14-line change to two files. |
| Policy denied a command | 7 | Six wrote to or read from outside the card's worktree (`/tmp/t.py`, `../p.diff`, `ls ..`). One was over-cautious: `sed -n '/x/,$p'` was refused as "a path built from a variable"; inside single quotes `$p` is literal. The builder used another command. |
| Policy decisions recorded | 1,996 | Every file read, write and command, with the decision and its reason, in the run's journal. |

Plain Claude Code ran with every permission and nothing checking its work; whatever it left
was handed over. Its journals exist only because the harness recorded the sessions.

## 2. Lesson 1: a rule the gate shows (Addendum 1)

Same card 5 times per setup, on one snapshot; the hidden check is Rich's suite plus the
changelog rule that the project's `lint` gate enforces.

| Setup | Passes hidden check | Looks before first edit | Total time (s) |
| --- | --- | --- | --- |
| plain Claude Code | 0/5 | 3.2 | 53.6 |
| Claude Code, `/init` CLAUDE.md | 5/5 | 4.8 | 51.9 |
| PRAT, seeded knowledge | 5/5 | 3.8 | 59.2 |
| PRAT, after the card and a dream | 5/5 | 3.0 | 59.9 |
| PRAT, seeded, selection fix | 5/5 | 2.2 | 52.5 |
| PRAT, learned, selection fix | 5/5 | 3.0 | 59.3 |

Plain Claude Code fixed the bug every time and never added the changelog entry. Every other
setup was told the rule before starting: PRAT lists the project's gates and their commands to
the builder, and `/init` found `tools/check_changelog.py` and wrote the rule into CLAUDE.md.
PRAT's builder ran the check itself before finishing in every run, so no PRAT run failed a gate
and the dream had nothing to learn. As Addendum 3 says, this measures being told the project's
rules, not learning.

## 3. Lesson 2: a rule taught once by a person (Addendum 3)

Same card 5 times per setup. The team's rule (every bug fix comes with a regression test marked
`@pytest.mark.regression` that fails without the fix) is written nowhere in the repository.
In the learning step a person sent each side's work back once with the same comment.

| Setup | Passes hidden check | Looks before first edit | Total time (s) |
| --- | --- | --- | --- |
| plain Claude Code | 0/5 | 3.8 | 45.4 |
| Claude Code, `/init` CLAUDE.md | 0/5 | 3.0 | 46.1 |
| Claude Code, CLAUDE.md after learning | **5/5** | 3.0 | 32.3 |
| PRAT, seeded knowledge | 0/5 | 3.0 | 55.3 |
| PRAT, after learning and a dream | **0/5** | 2.8 | 52.5 |
| PRAT, seeded, selection fix | 0/5 | 2.4 | 49.0 |
| PRAT, after learning and a dream, selection fix | **5/5** | 3.0 | 61.4 |

**Learning.** Both sides were sent back once and passed on their second try. PRAT's dream wrote
the rule as a convention entry, in its own words
(`memory/prat-knowledge/lesson2-learned/k4682140b46.md`):

> Every bug fix needs a regression test that fails without the fix and passes with it, marked
> @pytest.mark.regression. The team requires a regression test for each bug fix, and fixes
> without one are not merged. [...] The export_svg clip-width fix in rich/console.py was
> accepted only after a reviewer asked for this test.

Claude Code wrote the rule into CLAUDE.md, together with notes on this bug's code, including the
fixed line itself (`terminal_width=char_width * width - 1`) (`memory/claude-md/learned-lesson2`).
PRAT's entry names where the earlier fix was but not the fix.

**The PRAT bug the first "after" runs exposed.** With the rule learned, PRAT's builder still
missed it in 5 of 5 runs. The journals show why: the rule was not among the 8 entries (of 190)
chosen for the builder. Knowledge selection scored a note about a folder by the best file in
it, so the deep scan's notes about the whole `rich/` package (about 100 modules) tied with notes
about `rich/console.py` itself, and on that tie entries credited by earlier smooth cards came
first. The 6,000-character budget ran out one entry before the rule. Section 4 has the fix;
with it the rule reached the builder in every run and all 5 passed. It still does not reach
PRAT's reviewer, whose smaller budget (3,000 characters) fills first.

## 4. Deviations from the protocol, in the order they happened

1. **The knowledge-selection fix** (`src/prat/knowledge/select.py`). A note about a folder now
   scores by the best file under it divided by `1 + log2(files in the folder)`, so a package
   overview no longer ties with a note about the one file a card is about. A unit test
   reproduces the crowding-out and fails without the fix. The protocol's runs were made before
   the fix and are reported as they are; every PRAT setup that uses knowledge was then run
   again with the fix (the "selection fix" rows) on the same knowledge bases. The lesson 2
   learning step ran before the fix; selection does not change what the dream writes, only
   which entries a run is shown.
2. **Infrastructure.** Claude Code's first `/init` failed on read-only git files (a bug in the
   benchmark's own script), so the CLAUDE.md setups ran after the others. A reboot of the
   machine stopped two CLAUDE.md evaluations mid-round; neither had written a result, and both
   were run again from the start. No run was excluded for any other reason.
3. **Lesson 2's learning step** gave each side one rework round (both passed on it). PRAT's
   card was approved with the reviewer's verdict "partial", as a person may do; the person's
   check had passed.

## 5. What was checked for leaks

- No knowledge entry and no CLAUDE.md used for the main benchmark contains a test-batch bug's
  mutated line or a reference to its line number. Claude Code's learned notes describe only the
  learning-batch bugs; PRAT's dream added no lessons from the learning batch (all six cards
  passed first time, and the librarian learns from friction), only entries its checker split
  or brought up to date against the code.
- In both lesson experiments the "after" runs repeat the learning card on the same snapshot, as
  designed, so each side's memory may describe that bug. Section 3 says what each side kept.

## 6. Raw data

| Folder | What |
| --- | --- |
| `results/bench`, `results/lesson`, `results/lesson2` | `result.json` of every evaluation (`results/<label>.json`), the analysis (`analysis.json`) and every run's journal (`journals.tar.gz`) |
| `memory/prat-knowledge/<base>` | every PRAT knowledge base used, as entry files |
| `memory/claude-md/<name>` | every CLAUDE.md used, with notes on the sessions that wrote it |
| `memory/prat-learning` | PRAT's learning steps: each card's runs and every entry the dream added or changed |

This machine's folder names and user name are replaced by placeholders, and Claude Code's
start-up message has the account's connected services left out. `tools/analyze.py` recomputes
every table here from the results.
