# Exploratory runs

Runs made while building the evaluation harness, before the benchmark's protocol was written.
They were not pre-registered, so they are kept apart from the benchmark's results; they are
published so that nothing run on the way is left out.

| Folder | Cases | Setups | What it showed |
| --- | --- | --- | --- |
| [tinyshop](tinyshop/) | the 2 tinyshop cases, once each | plain Claude Code, PRAT, PRAT with seeded knowledge | all six runs fixed their bug; the whole system ran end to end |
| [rich](rich/) (`plain`, `prat`, `prat-knowledge`) | the 3 hand-planted Rich cases, once each | the same three | all nine runs fixed their bug |
| [rich](rich/) (`mem-*`) | the 3 hand-planted Rich cases, 3 rounds | plain Claude Code, PRAT with seeded knowledge, PRAT after three cards and a dream | all 27 runs fixed their bug; with knowledge after the dream, PRAT's builder made its first change sooner (7 s, against 9 s for both other setups) |

Each folder has a `result.json` per evaluation (`results/`) and every run's journal
(`journals.tar.gz`), with this machine's folder names and user name replaced by placeholders.

Two things to know when reading them:

- In the `mem-*` runs the dream added no new entries: the three cards it learned from went
  smoothly, and PRAT's librarian writes down lessons from friction (rework, fix lists, denied
  commands). The dream's other work, checking entries against the code, merging duplicates
  and crediting the entries that helped, is what changed between the two PRAT setups.
- In these runs the Rich module holding each planted bug had Windows line endings (the rest
  of Rich had Unix ones), an artefact of how the cases were built on Windows. The published
  case files have Unix line endings throughout, as do the benchmark's cases.
