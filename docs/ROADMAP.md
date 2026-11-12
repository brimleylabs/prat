# PRAT roadmap

Planned work, one level-two heading per item. Self-improvement reads this file from the
default branch and records each item as a finding; the items are taken in file order after
diagnostic findings. Each item has an optional `Effort:` line (low, medium or high), a short
description, and an `Acceptance criteria:` list.

A heading that ends in `(done)` is finished and skipped. A heading that ends in `(manual)` is
being built by a person and is skipped by self-improvement as well.

## Evaluation harness

Effort: high

Replay recorded cards against the scripted runtime and report pass rates per project and per
step, so a change to prompts, tools or policy can be shown to make agents better or worse.

Acceptance criteria:
- `prat eval run` replays a folder of recorded cards and prints pass rates per step
- a recorded card can be exported from a finished run with its journal and acceptance criteria
- results are stored so two evaluation runs can be compared side by side

## Project memory

Effort: medium

Per-project memory files with size caps, written after a card lands and read at the start of
the next card on that project.

Acceptance criteria:
- a landed card can add short notes to the project's memory file
- the memory file is capped in size and the oldest notes are dropped first
- the builder prompt includes the project's memory

## Skills

Effort: high

A SKILL.md format for reusable instructions. Agents can propose a skill; a person promotes it.
Skills are scoped to one project by default.

Acceptance criteria:
- skills are loaded from a folder per project and offered to the builder by description
- an agent can propose a skill, which is stored as a candidate and never loaded until promoted
- the web UI lists candidates with promote and reject actions

## Repo scout

Effort: medium

One read-only agent pass over a newly added project that drafts its `.prat/project.toml` and
notes from what it finds in the repository.

Acceptance criteria:
- `prat project scout <slug>` writes a draft project file for a person to review
- the scout has read-only tools and cannot write into the repository
- commands it proposes are checked against the repository's own files

## Card drafter

Effort: medium

Turn a vague request into a card with a clear goal and testable acceptance criteria.

Acceptance criteria:
- a draft card can be created from one line of text in the CLI and the web UI
- the drafter returns a goal and between one and eight acceptance criteria
- the person edits and confirms the draft before it becomes ready

## OpenTelemetry spans

Effort: medium

Emit OpenTelemetry spans for runs and steps following the GenAI semantic conventions, off by
default.

Acceptance criteria:
- with an exporter configured, each run and step produces a span with model and cost attributes
- with no exporter configured, nothing is imported or sent
- prompt and file contents are never put in span attributes

## Secret store

Effort: medium

Encrypt stored secrets with the operating system keyring when available, or a key file in the
PRAT home otherwise.

Acceptance criteria:
- secrets in the PRAT config can be stored encrypted and are decrypted only when used
- the keyring is used on Linux, macOS and Windows when present, with a key file fallback
- no secret is ever written to logs, journals or the error log

## GitHub integration

Effort: high

Turn issues into cards, and optionally publish a card branch as a pull request.

Acceptance criteria:
- issues with a chosen label can be imported as draft cards
- publishing a pull request is an explicit action by a person, never automatic for other projects
- credentials come from the person's existing git or gh setup, never from PRAT's database

## Plugin interface

Effort: high

A small plugin interface for adding pages and background tasks, with an insights plugin as the
first user.

Acceptance criteria:
- plugins are discovered from installed packages through an entry point
- a plugin can add a read-only page and a scheduled task
- the insights plugin shows cost and success rates per project over time

## One-command install and docs site

Effort: medium

Install PRAT with one command on Linux, macOS and Windows, and publish the docs as a static site.

Acceptance criteria:
- one documented command installs PRAT and creates the PRAT home
- the docs build into a static site from the docs folder
- the install is tested on all three operating systems

## Workspace provider interface (manual)

Effort: high

One interface (create, snapshot, inspect changes, validate, promote, discard) that the git
worktree implements today, so other versioned state can be added as providers.

Acceptance criteria:
- the engine uses the interface for every worktree operation
- the git worktree provider passes the existing test suite unchanged

## Database branch per card (manual)

Effort: high

A workspace provider for Dolt and Doltgres that gives each card its own database branch, shows
the schema and row diff on the review page, merges on approve and drops on reject.

Acceptance criteria:
- a card on a project with a database gets its own branch of that database
- the review page shows the schema and row diff
- approve merges the branch and reject drops it

## Dolt store for PRAT (manual)

Effort: high

PRAT's own store on Dolt, with a commit per run step and per improvement cycle, and history
and rollback views. SQLite remains available.

Acceptance criteria:
- PRAT runs on Dolt when the Dolt binary is present and on SQLite otherwise
- each run step and improvement cycle is a commit
- history and rollback are available from the web UI

## Limits beyond dollars

Effort: medium

Maximum duration, tool calls and turns per run, enforced by the engine.

Acceptance criteria:
- a project and a card can set a maximum duration, number of tool calls and number of turns
- a run that reaches a limit stops cleanly and the card says which limit was reached
- the limits are shown on the card before it runs

## What this card can do panel

Effort: low

A panel on the card page that shows, before a card runs, its effective autonomy, tools,
writable and protected paths, network access and limits.

Acceptance criteria:
- the panel is computed from the same policy the run will use
- it is shown on the card page for cards that have not run yet
- it updates when the card's autonomy or the project file changes

## Tamper-evident journal

Effort: medium

Each journal entry carries the hash of the previous entry, with a command that verifies a
journal.

Acceptance criteria:
- every journal entry includes the hash of the entry before it
- `prat journal verify <run>` reports the first entry that does not match
- old journals without hashes are reported as unverified, not as tampered

## Project templates per stack

Effort: medium

Templates with gates, protected paths and change rules that fit common stacks (Python,
Laravel, WordPress, React, Go and others), chosen when a project is added.

Acceptance criteria:
- `prat project add` offers a template that matches the repository it detects
- each template validates against the project file schema
- a template never sets autonomy above medium

## Counterfactual runs

Effort: high

In the evaluation harness, re-run a recorded card from the same base commit with a different
model or settings and compare the results.

Acceptance criteria:
- a recorded card can be replayed with a chosen model or setting changed
- the comparison shows gate results, verdict and cost for both runs
- replays never touch the original card or branch

## Provenance on every run

Effort: low

Record the model identifier, prompt version and a hash of the effective policy with every run.

Acceptance criteria:
- each run stores the model identifier, a prompt version and a policy hash
- the card page shows them for each run
- the policy hash changes when the effective policy changes and only then
