# Feature benchmark: protocol

Written on 2026-11-23, before any of the runs it describes. The results are in
[RESULTS.md](RESULTS.md) and every run's record is under [results/](results/).

## Question

On building a feature (not fixing a bug), how long does PRAT take from a ready card to a result
in review, compared with one plain Claude Code session given the same text? And what do two of
PRAT's features change: a swarm on a wide job, and a fact pack on a ticket that names no files?

## Cases

All three are in this repository. Each has a card, a starting code base and a hidden check the
agent never sees.

| Case | What it is | Hidden tests |
| --- | --- | --- |
| [swarm/giftcards](../swarm/giftcards/) | one small feature in a 23-file shop backend | see the case |
| [swarm-xl/megastore](../swarm-xl/megastore/) | eight independent modules from a 4,400-word specification | 113 |
| [packbench/coverage-stale-files](../packbench/coverage-stale-files/) | a feature across a schema, a query layer and a command in coverage.py, from a ticket that names no files | 47 |

## Conditions

| Name | What runs |
| --- | --- |
| `plain` | One Claude Code session with the card's text and the project's notes. No policy, gates, reviewer or rework. |
| `single` | PRAT as released, the card as one card: builder, gates, reviewer, rework on failed gates (up to 3 runs). |
| `swarm` | PRAT, the card as an epic: the planner splits it, the split is approved at once, the parts are built at the same time and the epic comes to review once. |
| `single+pack` | As `single`, after PRAT has researched and checked a fact pack for the card, as it does for cards waiting in the backlog. The research time is recorded separately and is not part of the build time. |

Which conditions run on which case:

- giftcards: `plain`, `single`
- megastore: `plain`, `single`, `swarm`
- coverage-stale-files: `plain`, `single`, `single+pack`

## Settings

PRAT's defaults at the commit named in the results, with nothing tuned for the benchmark: the
default models, builder effort `low` on a first attempt, `race_builders = 2`. The runner raises
the limit on runs at once to 12 so that a swarm's parts are not queued. The plain baseline uses
the same default model. Authentication is a Claude login.

## Runs

- Five runs of every condition on every case, on Windows 11. The megastore case is repeated
  five times per condition on macOS as a check on a second machine.
- Runs go one at a time. They are ordered in rounds (every condition once, then every condition
  again) so that a slow hour does not land on one condition.
- A run is limited to 25 minutes.

## What is measured

- **Seconds**: wall-clock time from starting the card to the result (a card in review, or the
  plain session ending). For a swarm this includes the split.
- **Hidden check**: passed or failed, on the commit the run produced.
- **Outcome**: the card's final status. A run that fails, times out or errors is a failed run.
- **Model cost in US dollars**, as the runtime reports it.
- For `single+pack`: the seconds spent preparing the pack, reported beside the build time.

## Reporting rules

- Every run is reported. None is dropped or repeated to replace a bad one. If a run is lost to
  something outside the test (a power cut, a rate limit refusal), it is listed as lost with the
  reason, and one replacement is run.
- For each condition: the median, the fastest and the slowest time, and how many of the five
  passed the hidden check. Times of failed runs are shown but not counted in the median.
- No significance is claimed from five runs. The ranges are the result.

## What this does not show

- It is three cases on two code bases, chosen while building the features they test. It says
  nothing certain about other projects.
- The plain baseline has no review, so its time is not time to the same assurance. The
  [case study](../../docs/CASE-STUDY.md) covers what PRAT's checks caught on real bugs.
- Timings depend on the model service on the day.
