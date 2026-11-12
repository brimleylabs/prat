# Project knowledge: what PRAT learns, how agents use it, how it stays true

PRAT's agents should get better at a project with every card, without being handed everything
PRAT knows. This design combines approaches that have been shown to work:

- **A compiled wiki over immutable sources** (Karpathy's LLM knowledge base): raw evidence is
  never edited; an agent writes and maintains short linked entries from it, every claim cites
  its source, and regular lint passes look for contradictions and gaps.
- **Incremental, itemized updates with feedback** (Agentic Context Engineering, ICLR 2026):
  knowledge grows as small entries changed by deltas, never rewritten wholesale, which avoids
  context collapse; a reflector proposes and a curator merges; each entry counts how often it
  helped or hurt.
- **Sleep-time consolidation** (Letta): merging, deduplication and reorganisation happen in idle
  time with a small model, not during the person's day.
- **Facts are invalidated, not deleted** (Graphiti): a superseded entry records when and by what
  it was replaced, and every entry links back to the evidence it came from.
- **A ranked code map within a budget** (Aider's repository map): a graph of files and imports,
  built without a model, ranked by PageRank toward what the card is about and cut to a token
  budget.

## Storage

Per project, in `$PRAT_HOME/knowledge/projects/<slug>/`, a git repository of its own:

```
MEMORY.md                 index: one line per active entry, written by PRAT
entries/<id>.md           one entry: front matter and a short body
```

Front matter: `id`, `kind` (`convention`, `pitfall`, `command`, `decision`, `layout`),
`summary` (one line), `paths` (repository paths it is about), `links` (ids of related entries,
with a relation: `related`, `refines`, `contradicts`), `sources` (card and run ids, files),
`status` (`active`, `stale`, `retired`, `superseded`), `superseded_by`, `confidence`
(`observed`, `confirmed`), `helped`, `hurt`, `created`, `last_verified`, `invalid_at`.

Files are the source of truth, readable and editable by a person; PRAT's database keeps an index
for queries and the graph. Every change is a commit in the knowledge repository, so the history
of what PRAT believed, and why, is always there.

The code map is computed, not stored as entries: files, the imports between them, and top-level
definitions, read from the project's default branch and cached by commit.

## Writing

During the dream's Remember phase, for each card that finished since the last one, a small
read-only model session (the librarian) reads the card, the diff summary, the reviewer's verdict,
rework feedback, policy denials and the run's outcome, and proposes at most three deltas in a
strict schema: add an entry, refine one (with its id), or mark one wrong (with the evidence).
Rework feedback and people's comments are the richest source. A card with nothing to teach
proposes nothing.

The curator applies deltas without a model: it rejects anything with a secret, anything that
tries to change PRAT's rules (the research keyword screen), and entries over the size cap; it
merges a new entry into an existing one with overlapping paths and similar wording; a refinement
creates a new version and marks the old one superseded; an entry seen again from another card
becomes `confirmed`.

A person can add, edit, confirm or retire entries on the Knowledge page or with
`prat knowledge`. A person's entry is `confirmed` from the start and is never changed by the
librarian, only flagged.

## Reading

When a card starts, the builder, planner and reviewer get a section "What PRAT has learned about
this project", below PRAT's rules and the repository's own instruction files:

1. The candidate entries are the active ones. Each gets a score: confirmed before observed,
   `helped - hurt`, recency of verification, and above all relevance to this card.
2. Relevance comes from the graph: files named in the card or the plan seed a personalised
   PageRank over the code map; entries about the highest-ranked files, and entries linked to
   them, score highest.
3. Entries are added in score order until the budget (default 6,000 characters) is spent. The
   run records which entries it was given.

Each role sees what it needs: the builder and planner get conventions, commands, layout and
pitfalls; the reviewer gets conventions and pitfalls as a checklist for the files that changed.

## Feedback

When a run's card lands on the first try, each entry it was given gets `helped + 1`; when the
reviewer sends it back and the librarian finds an entry led the agent astray, that entry gets
`hurt + 1`. Entries with `hurt > helped` drop out of reading and are reviewed in the next dream.

## Maintenance (the Remember phase of every dream)

- **Ingest**: the librarian over finished cards, as above.
- **Verify**: every entry whose paths changed in landed work since it was last verified, or that
  has not been verified for 30 days, is checked: paths that no longer exist are followed through
  renames or the entry becomes `stale`; a small model reads the current files for entries whose
  files changed and says whether the entry still holds.
- **Lint**: entries that contradict each other, duplicates, and files the agents touch often with
  no entry about them, are reported; contradictions go to a person.
- **Consolidate**: duplicates merged, entries stale for 30 days retired, the index rebuilt within
  its cap.
- **Map**: the code map is rebuilt for projects whose default branch moved.

Every dream records what each step did; the Improve page shows recent dreams.

## Surfaces

- **Knowledge page** per project: an interactive graph of entries, the files they are about and
  the project itself (for entries about all of it), plus the files the code leans on most so
  gaps show (filter by kind and status, click for an entry's text, sources, history and links),
  the list view with confirm and retire, a form to add an entry as a person, and recent changes.
  The page polls a small version endpoint and redraws when entries or the default branch change.
- **Improve page**: the rhythm heatmap, the dream window, dream hours and every dream's results.
- **CLI**: `prat knowledge list | show | add | confirm | retire | graph | reindex` and
  `prat dream status | now | hours`.

## The scout (a first look)

Before any card has run, a project can be seeded: one read-only session (Read, Glob, Grep under
PRAT's read-only policy, on PRAT's own clone, never the person's checkout) reads the repository
and proposes at most twelve `add` deltas in the librarian's format. The curator screens them as
usual; they are authored `scout`, stay `observed`, and the session is journaled under
`$PRAT_HOME/journal/seed-<slug>-<time>.jsonl`. Seeding again tells the scout what is already
known, so it adds rather than repeats.

## Where it is in the code

`prat.knowledge` (entries, codemap, select, provide, curator, librarian, maintain, graph, seed),
`prat.rhythm`, `prat.dream`; the engine calls `prat.knowledge.provide.for_card` before each
planner, builder and reviewer session and records the entry ids in the step detail.
