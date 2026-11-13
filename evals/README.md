# Benchmark cases

Case sets for `prat eval run` (see "Evaluating agents" in [docs/GUIDE.md](../docs/GUIDE.md)).
Every case is a card, a starting codebase and a hidden check the agent never sees.

| Set | Codebase | Cases | What it exercises |
| --- | --- | --- | --- |
| [rich](rich/) | [Rich](https://github.com/Textualize/rich), about 100 modules | 3 | finding a bug from its symptom in a real library |
| [tinyshop](tinyshop/) | a small shop backend written for these cases, 23 files | 2 | tracing a symptom through several modules |

Each set runs in seconds per case on a laptop, so a full comparison takes minutes.

## Running them

```bash
# PRAT, with a real model (builder, gates, reviewer, rework)
prat eval run evals/rich --runtime claude --label prat

# the same cases with plain Claude Code: one session, no policy, gates, reviewer or rework
prat eval run evals/rich --runtime plain --label plain

# PRAT with what it knows about the project (seed it first with `prat knowledge seed`)
prat eval run evals/rich --runtime claude --knowledge ~/.prat/knowledge/projects/rich

prat eval compare <plain-id> <prat-id>
```

Add `--jobs 3` to run the cases of a set at once.

## How a case is built

The agent gets the card's title, goal and acceptance criteria, and the project's notes. The
bug is described by its symptom, never by its place in the code. The hidden check runs the
project's own tests, including the ones that catch the bug; those tests are taken out of the
copy the agent works on. Every case is verified three ways before it is used: the visible
tests pass with the bug present, the hidden check fails with it, and the hidden check passes
once it is fixed.
