# PRAT architecture

This document describes Phases 1 to 3: what exists, how the pieces fit, and why the main
decisions were made. It is written for contributors and for whoever builds the next phases.

## The loop

```
card (ready) -> run: prepare -> plan -> execute -> verify -> critique -> summarize -> finalize
             -> card (review | rework | blocked | failed | cancelled)
review -> approve -> local --no-ff merge into the agent clone (never pushed)
review -> human comment + rework -> new run on the same branch
critique says not_met or wrong -> card goes to rework with a fix list -> run again
```

One card gets one branch, `card/<id>-<slug>`. Each run of the card gets a fresh worktree on that
branch under `$PRAT_HOME/worktrees/<project>/card-<id>`. The worktree is removed when the run
finishes; the branch keeps the work, so a rework or a retry after `blocked` continues where the
last run stopped.

## Modules

```
src/prat/
  config.py          installation settings (env, TOML, defaults); derived paths
  domain.py          enums and the card, run and step state machines
  paths.py           symlink- and case-aware path comparison, glob matching
  proc.py            child processes that can be killed as a whole tree (gates, git, probes)
  projects.py        .prat/project.toml schema (pydantic), slugs, path separation rules
  store.py           data-access helpers; every status change also writes an audit event
  db/
    models.py        SQLAlchemy 2 typed models with transition validators
    database.py      engine, SQLite pragmas (WAL, foreign keys), sessions
    migrate.py       programmatic Alembic upgrade and revision checks
    alembic/         migration environment and versions (shipped inside the package)
  git/
    runner.py        git wrapper: argument lists only, no prompts, ambient GIT_* stripped
    worktree.py      agent clone, push blocking, worktrees, safety commit, diff stats, merge
  policy/
    shell.py         conservative shell tokenizer (operators, redirects, substitutions)
    secrets.py       credential-shaped literal detection, diff scanning
    engine.py        Policy.decide(): one decision function for every tool path
  model_registry.py  agent roles to model IDs, overrides, per-token price estimates
  runtime/
    base.py          AgentRuntime protocol, AgentTask, typed events
    fake.py          scripted deterministic runtime used by tests and demos
    claude.py        Claude Agent SDK adapter (tool restriction, policy hook, event mapping)
    auth.py          api_key and claude_login modes, fail-fast checks, child environment
    loop.py          loop detector in front of the policy
    session.py       run one session to the end and collect its answer (planner, critic)
  journal.py         append-only JSONL journal per run, tolerant reader
  verification.py    gates with timeouts and logs, empty/oversize/protected/secret checks
  changes.py         deterministic change classifier: categories and risk per changed file
  errors.py          the error log: capped, redacted rows with stable fingerprints
  improve/
    settings.py      the self-improvement toggle and its limits, stored in app_settings
    events.py        the activity feed: one row per decision the loop makes
    selfproject.py   PRAT's own repository as a hardened clone; setup and sync
    findings.py      findings stored once per fingerprint; dismiss, stale
    diagnostics.py   metrics, health checks and finding rules (no model cost)
    roadmap.py       docs/ROADMAP.md read from the default branch
    research.py      the researcher session, its output schema and keyword screen
    cycle.py         one cycle: sync, diagnose, research, rank, file cards
    scheduler.py     the background task in prat serve; may_start()
    invariants.py    the invariant list; export and isolated run of the trusted copy
    trial.py         the staged trial of a candidate commit
    guard.py         land or refuse, from evidence only
    landing.py       prove, merge, push; rework, abandon, hold; outcome check
  build.py           which commit is running (for /healthz)
  evals/
    cases.py         evaluation cases: case.toml, starting files, hidden check, scripted attempts
    runner.py        each case through the engine in a home of its own; the hidden check
    results.py       scoring each attempt and case, stored results, side-by-side comparison
    export.py        a finished card as a case that replays its runs
    plain.py         plain Claude Code on a case's card: the baseline
    suite/           the built-in cases (sample projects, not PRAT code: no lint or types)
  engine/
    orchestrator.py  Engine: queue, steps, cancel, resume, approve, rework
    limits.py        global and per-project concurrency slots
    interfaces.py    Reviewer and Planner protocols and results, FakeReviewer, NoPlanner
    planner.py       AgentPlanner: read-only plan pass for complex cards
    critic.py        AgentReviewer: read-only review session, verdict parsing
    instructions.py  reads AGENTS.md and CLAUDE.md from the worktree root, bounded and link-safe
    prompts.py       every prompt: builder, planner, critic (pure functions)
    summary.py       run summary rendered from DiffStats
  web/
    app.py           FastAPI factory, template environment, lifespan (recover, cancel on stop)
    security.py      middleware: Host, session, Origin, content type, CSRF; security headers
    tokens.py        access token file, one-time login codes, login URLs
    sessions.py      sessions and used login codes in the database, flash messages, sign-in throttle
    data.py          database reads that build board, card, run and project view models
    views.py         formatting, diff parsing, journal entry presentation, redaction
    routes_pages.py  GET pages and fragments
    routes_actions.py POST actions: create, run, cancel, rework, approve, comment, projects
    routes_stream.py server-sent events: board changes, live session entries
    server.py        deferred Claude runtime, uvicorn runner
    icons.py         inline SVG icons (the only markup built outside templates)
    templates/       Jinja2, autoescaped
    static/          CSS, the UI script, theme script, vendored htmx
  cli.py             typer command line (including serve, login-url, rotate-token)
```

Dependencies point one way: `domain`, `paths`, `proc` and `model_registry` depend on nothing in
PRAT; `config` depends on those; `policy`, `git`, `projects`, `journal` and `verification` depend only
on the layers below; `runtime` depends on `policy` (and `auth` on `config`); `engine` ties
everything together; `cli` sits on top. Only `runtime/claude.py`, `runtime/auth.py` and the CLI
know about the Claude Agent SDK; the engine, planner and critic speak the `AgentRuntime`
protocol and are tested with the fake runtime.

## State machines

All legal transitions live in `domain.py`. The ORM models call `check_transition` from
`@validates("status")`, so an illegal change raises `InvalidTransition` whichever code path
attempts it, including new code that forgets to ask.

**Card**

| From | To |
| --- | --- |
| (new) | draft, ready |
| draft | ready, cancelled |
| ready | running, draft, cancelled |
| running | review, rework, failed, blocked, cancelled |
| review | done, rework, cancelled |
| rework | running, cancelled |
| blocked | ready, rework, cancelled |
| failed | ready, rework, cancelled |
| cancelled | draft |
| done | (terminal) |

`blocked` can never go straight to `review`: a blocked card has to run again. `running ->
rework` happens only when the reviewer's verdict is `not_met` or `wrong`; the run itself still
ends `succeeded` (it did its job and passed the gates), and finalize sends the card back
instead of to review.

**Run**: `queued -> running -> succeeded | failed | blocked | cancelled`, plus
`running -> interrupted -> running | cancelled | failed` for crash recovery.

**Step**: `pending -> running -> succeeded | failed | skipped`, `running -> pending` when an
interrupted run is recovered, and `failed -> running` for a retry.

## Data model

| Table | Holds |
| --- | --- |
| `projects` | slug, name, `checkout_path`, `agent_path`, default branch, snapshot of the project file |
| `cards` | title, goal, acceptance criteria, complexity, status and reason, optional autonomy and budget overrides, branch, diff summary |
| `runs` | attempt, status, outcome, reason, runtime name, session id, effective autonomy, budget and spend, worktree, branch, base/start/head commits, spec snapshot, feedback, summary, journal path, cancel request, heartbeat, owner |
| `run_steps` | one row per step per run: status, attempts, detail (JSON), error, timestamps |
| `comments` | human feedback, reviewer verdicts, system notes (merge conflicts) |
| `events` | audit trail of every status change and notable action |
| `web_sessions` | one row per signed-in browser: SHA-256 hash of the session id, CSRF token, fingerprint of the access token it was created with, creation and expiry times |
| `login_nonces` | SHA-256 hash of each used one-time login code's nonce, kept until the code has expired |

Only generic SQLAlchemy types are used (enums are stored as strings, JSON uses the generic
`JSON` type), so the same migrations run on Postgres. SQLite is opened in WAL mode with foreign
keys on. The access token, API keys and session ids are not stored in any table: a session row
holds only a hash of its id, which cannot be turned back into a cookie. A session's CSRF token
is stored as it is; it is useless without the cookie.

A test compares the Alembic head against the models (`compare_metadata`) so the two cannot drift.

## Paths and isolation

- `checkout_path` is the person's own clone. PRAT clones from it and fetches from it; no agent
  ever runs there and nothing writes to it. Approval merges into the agent clone; the person
  pulls from there.
- `agent_path` is PRAT's clone. Its default branch stays checked out in its main directory; card
  worktrees live elsewhere, under `$PRAT_HOME/worktrees`.
- Project registration refuses overlapping paths (agent clone inside the checkout, or one
  project inside another). Before every run the worktree path is checked against every
  registered checkout.
- Path comparisons resolve symlinks (and Windows junctions) and fold case when the filesystem
  is case-insensitive. Case sensitivity is probed on the real filesystem rather than guessed
  from the platform.

### Push is structurally impossible

The agent clone is configured so that no push can leave it, whatever the command looks like:

1. `remote.origin.pushurl` is set to `prat-push-disabled://`, a scheme with no transport.
2. `url."prat-push-disabled://".pushInsteadOf` is set to the empty string, which rewrites
   every push destination (named remote, plain path, `file://` URL) to that scheme.
3. A `pre-push` hook in the shared hooks directory refuses, as a last layer.

Worktrees share the clone's configuration, so this applies in every card worktree. An
integration test runs eight real `git push` variants from a worktree, including `--no-verify`
and explicit paths to the person's checkout, and asserts that all fail and the checkout's refs
are unchanged. The policy also denies push commands, but it is the second line of defence, not
the first.

## Policy

`Policy.decide(ToolCall) -> Decision(allowed, reason)` is the single entry point for shell
commands, file reads, file writes and MCP tools. A runtime adapter must call it before every
tool use; the engine wraps it to journal each decision.

Autonomy levels (effective level = lowest of installation ceiling, project file and card):

| Level | Shell | Files |
| --- | --- | --- |
| `read_only` | inspection commands only (`ls`, `cat`, `grep`, `git status/log/diff`...) | read |
| `low` | plus file commands, local git writes, and the project's own commands | read, write in worktree |
| `medium` | plus any local program, script or inline interpreter code | same |
| `high` | plus network tools and package installs | same |

Denied at every level: `git push` in any form (including `send-pack`, `lfs push`, aliases via
`-c alias.*`, inline interpreter code that mentions it), remote and config changes, worktree
management, branch deletion, history rewriting, privilege escalation, deployment and cloud CLIs,
publish commands, system administration, `eval`, shells reading commands from standard input
(`curl ... | sh`), setting `GIT_DIR`-style variables, `git -C` or `--git-dir` outside the
worktree, any write outside the worktree or to a protected path, any read or write of a secret
file, and any write or command containing a credential-shaped literal.

How commands are analysed: the command line is tokenized with `shlex` (punctuation aware,
comments disabled so `#` cannot hide a second line). It is split at `&&`, `||`, `;`, `|`, `&`,
newlines and parentheses. `$(...)`, backticks and `<(...)` are analysed recursively, including
inside double quotes. Wrappers (`env`, `timeout`, `nohup`, `xargs`, `nice`...) are unwrapped,
`sh -c`, `bash -lc`, `cmd /c` and `powershell -Command` are parsed recursively, `find -exec` is
analysed as its own command, `cd` updates the working directory used for later path checks,
and redirection targets are checked as writes. Anything that cannot be parsed is denied.

MCP tools must be registered with an `McpToolSpec` declaring what they do (`read`, `write` or
`exec`) and which arguments hold paths, content or a command. They are then judged by the same
file and shell rules. Unregistered MCP tools are denied. This closes the gap where a "run tests"
tool takes a command string and runs it outside the shell policy.

## Runtime interface

```python
class AgentRuntime(Protocol):
    name: str
    def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]: ...
    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]: ...
    async def cancel(self, session_id: str) -> None: ...
```

`AgentTask` carries the prompt, working directory, allowed tool names, the policy callback, a
budget and metadata. Events: `SessionStarted`, `TextEvent`, `ToolCallEvent`, `ToolResultEvent`,
`CostEvent` (estimated by default), `BlockedEvent`, `DoneEvent(outcome)`. Outcomes: completed,
blocked, budget_exceeded, error, cancelled.

`FakeRuntime` executes a JSON script (`text`, `read`, `write`, `edit`, `bash`, `mcp`, `cost`,
`wait`, `finish`, `block`, `exceed_budget`, `error`, `crash`). It calls the policy exactly as a
real adapter must, stores its cursor per session id on disk so `resume` continues after a crash,
and records what it executed so tests can prove that denied or post-cancel actions never ran.
`crash` raises `FakeCrash`, a `BaseException`, so nothing in the engine handles it and the
database is left mid-run, as after a real process death.

## Claude runtime adapter

`runtime/claude.py` implements `AgentRuntime` on the Python Claude Agent SDK
(`claude-agent-sdk`, tested with 0.2.180, which bundles Claude Code 2.1.321). The SDK runs the
unmodified Claude Code executable as a subprocess and talks to it over stdin and stdout.

**Tool restriction.** The SDK has three tool options that look alike. `allowed_tools` only
pre-approves tools (no permission prompt); it does not restrict anything, so PRAT never sets it.
`tools` is the base set the model can see at all; PRAT sets it from the task's tool names
(`read` to `Read`, `glob` to `Glob`, `grep` to `Grep`, `write` to `Write`, `edit` to `Edit`,
`bash` to `Bash`). `disallowed_tools` removes tools from the model's context; PRAT lists every
other built-in tool there as well (web tools, subagents, skills, notebooks and so on).

**Every call goes through the policy.** A `PreToolUse` hook with no matcher (so every tool,
MCP tools included) translates the call into one or more `ToolCall`s and asks the task's policy
callback, which the engine wraps with the loop detector and journaling:

| Claude Code tool | PRAT policy call |
| --- | --- |
| Read | read of `file_path` |
| Write, Edit, MultiEdit, NotebookEdit | write of the path with the new content |
| Bash | shell command (`run_in_background` is denied outright) |
| Glob, Grep | read of `path`, plus the literal prefix of a pattern that leaves the root |
| `mcp__server__tool` | MCP call, judged by its registered `McpToolSpec` |
| anything else, or a tool the task did not enable | denied |

An allowed call returns `permissionDecision: allow`; a denied one returns `deny` with the
reason, which Claude Code hands to the model as the tool's error, and the adapter reports the
tool result as `denied` with that reason. The hook matcher uses `on_failure="block"`: by default
the SDK treats a hook that raises as "no decision", which would let the call through, so PRAT
makes failures deny. The decision function itself never raises (an exception becomes a denial).
A `can_use_tool` callback denies anything that would otherwise reach an interactive permission
prompt; with the hook answering every call it should never be reached.

**Isolation from the machine's Claude Code setup.** `setting_sources=[]` stops the user's and
project's Claude Code settings (their hooks, permission rules, plugins, memory files) from
loading, and `strict_mcp_config=True` with no servers keeps MCP servers from user configuration
out. Project knowledge reaches the agent through the prompt instead: the project file's facts
and the repository's `AGENTS.md` and `CLAUDE.md`, which PRAT reads itself (see Prompts). The
system prompt is Claude Code's own preset with PRAT's builder, planner or critic prompt appended, so the agent
keeps the tool-usage guidance of the harness.

**Event mapping.** The init system message becomes `SessionStarted` (session id, the tool list
Claude Code reports, the model); text blocks become `TextEvent`; tool use blocks become
`ToolCallEvent`; tool result blocks become `ToolResultEvent`; each new assistant response's
`usage` becomes an estimated `CostEvent` priced by `model_registry` (deduplicated by message
id), and the final `ResultMessage` adds a correction so the run's total matches the session's
own `total_cost_usd`. Result subtypes map to outcomes: `success` to completed (or blocked, see
below), `error_max_budget_usd` and `error_max_turns` to budget_exceeded, anything else to
error. Connection failures and a stream that ends without a result are errors, never success.

**Budget, resume, cancel.** The remaining budget goes to the SDK as `max_budget_usd` (and the
turn limit as `max_turns`), and the engine still enforces the budget itself from cost events.
The session id is recorded as soon as the init message arrives; `resume` passes it as the SDK's
`resume` option. Messages are read by a pump task into a queue; the event stream waits on that
queue or on a cancel flag. `cancel(session_id)` sets the flag and sends an interrupt, and the
stream then disconnects the client, which terminates the Claude Code process. Cancelling the
consuming task has the same effect.

**Authentication** (`runtime/auth.py`). `api_key` puts the key in the child's environment;
`claude_login` blanks `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` for the child so Claude Code
uses its owner's login, and checks readiness by running `claude auth status --json` and reading
only `loggedIn` and `authMethod`. `ClaudeRuntime.from_settings` refuses to start when the mode
is not usable. The SDK can only override environment variables, not remove them, which is why
the key variables are blanked rather than unset.

## Models

`model_registry.py` maps roles to model IDs: Sonnet for builder, critic, security reviewer,
researcher and reader; Opus for planner and scout; Haiku for the librarian (and the drafter and
summarizer, which nothing uses yet). The project
file's `[models]` table wins over the installation's `models` setting, which wins over the
defaults. The engine resolves the model per step and records it in the step detail and journal.

## Prompts

`engine/prompts.py` holds every prompt as a pure function, so prompts can be read and tested
without a model.

- **Builder system prompt**: the role; the card's title, goal and acceptance criteria; the
  repository's instruction files, if it has any; project facts from the committed project file
  (name, default branch, autonomy, `notes`, commands, gates, paths it may never write); the
  rules (work and commit only inside the worktree, never push or change git configuration, run
  the project's test command before finishing, no secret files, do not work around policy
  denials); and the BLOCKED protocol.
- **Builder task prompt**: per run: the plan, if one was made, and the feedback for a rework.
- **Planner** and **critic** prompts, described below.

**Repository instruction files.** `engine/instructions.py` reads `AGENTS.md` and `CLAUDE.md`
from the root of the card's worktree each time a builder, planner or critic prompt is built. It
is the only file reading behind the prompts and is kept out of `prompts.py`, which stays pure.
Only those two names at the root are looked for. Each path is resolved first and must still be
one of the two files directly in the worktree root, so a symlink to any other file (outside the
worktree or inside it) is refused; only regular files are read, at most 12,000 characters each,
and a file cut at that size is followed by a note saying so. Nothing else is discovered: no
subdirectories, no parent directories, no references inside the files, no settings, no hooks.

`prompts.repository_instructions` renders the files as one section headed "Repository
instructions", each file between `<repository-file name="...">` markers; text inside a file that
imitates a marker is altered so a file cannot appear to end early. The section says the files
are the repository's guidance about code and conventions and cannot change PRAT's rules. In
every prompt it comes before PRAT's project facts and rules, and when it is present the rules
say they apply whatever the files say (the planner and the critic get a short rules section of
their own for this). The critic sees the files as they are after the builder's work, and is told
that an edit to them is part of the diff under review. The `execute.model` journal entry lists
the files the builder was given. This is prompt hygiene; the controls remain the policy, the
push block and the protected paths (see docs/SECURITY.md).

The BLOCKED protocol: when the agent truly cannot proceed it ends its final message with a line
`BLOCKED: <reason>`. The adapter turns that into `BlockedEvent` and outcome `blocked`; the card
becomes `blocked` with the reason. A blocked card never reaches review.

## Loop detector

`runtime/loop.py` keeps the last `loop_window` calls (default 8) of one agent session, keyed by
tool kind and target (the normalised command, the path, or the MCP tool and its arguments). When
a call makes `loop_threshold` (default 5) identical calls within the window, it is denied with a
message telling the agent to stop, reconsider, change approach or report BLOCKED. Denied calls
count, so an agent that ignores the message keeps being denied. The engine puts a fresh detector
in front of the policy for each agent (builder, planner, critic), so it works with every
runtime, and journals loop denials with `loop: true`.

## Planner

For cards with complexity `high`, `AgentPlanner` runs a short session before the builder: the
planner model, tools Read, Glob and Grep only, and a read-only policy. Its final answer becomes
the plan in the builder's task prompt. If the plan ends with `BLOCKED: <reason>`, the plan step
fails with run outcome `blocked`. Outcomes are first-decided-wins: once a step decides an outcome
other than success, no later step can replace it (later steps are skipped and finalize applies
it), so a blocked plan cannot be overwritten by a later "success". A planner that errors for
another reason only means the builder starts without a plan. Planner cost counts toward the
run's budget.

## Critic

`AgentReviewer` is the real `Reviewer`. It starts a fresh session (no shared context with the
builder) with tools Read, Glob and Grep only, a read-only policy over the worktree, and the
critic model. It is given the card's acceptance criteria, the summary computed from the diff,
the gate results with the end of their output, the names of test functions added in the diff,
and the diff itself (truncated with a note; it can read files for the rest). It is never shown
the builder's own account of its work.

The prompt asks for a JSON verdict: `verdict` (`met`, `partial`, `not_met`, `wrong`), `summary`,
per-criterion findings with evidence, a `fix_list` and `confidence`. Parsing tries fenced JSON
blocks (the last one wins), then any bare JSON object with a `verdict`, then a `verdict: <word>`
line, and normalises aliases. A `met` verdict that marks a criterion `not_met` is downgraded to
`partial`. An answer that cannot be read, or a session that errors or runs out of budget,
becomes `partial` with `parsed: false`: a broken reviewer puts the card in front of a person and
never approves or rejects on its own.

The engine posts the verdict as a reviewer comment. For `not_met` and `wrong` it also posts the
fix list as a `fix_list` comment, marks the critique step `rework: true`, and finalize moves the
card to `rework` instead of `review`.

## Rework

A card goes back for rework in two ways: the reviewer's `not_met` or `wrong` verdict, or a
person adding a comment (`prat card comment`) and moving the card from review to rework
(`prat card rework`, or `Engine.rework`, which refuses without a new comment or feedback text).
The next run continues on the same branch. Its feedback, stored on the run and put in the
builder's task prompt (and shown to the next reviewer), is the human comments added since the
previous run plus the previous reviewer verdict with its findings and fix list. Nothing reruns
automatically: a card in rework waits for `prat card run` (or, later, the UI).

## Engine

- **Queue and limits.** `Engine.submit(card_id)` returns an asyncio task. A run first takes a
  per-project slot and then a global slot, so a busy project never holds global capacity while
  it waits. Git operations that change a clone (prepare, cleanup, merge) also take a
  per-project lock.
- **Persisted steps.** All seven step rows are created with the run. Before a step runs its row
  becomes `running` with an incremented attempt count; afterwards it records status, detail and
  error. The first non-success outcome decided by a step is stored on the run; later steps are
  marked `skipped` and `finalize` always runs.
- **Outcome to card status.** succeeded to review, blocked to blocked (with the agent's
  question as the reason), failed to failed, cancelled to cancelled. A blocked run can never
  produce `review`.
- **Execute.** Streams runtime events into the journal, records the session id as soon as it
  is known, adds up cost and stops the runtime when the budget is exceeded (the engine enforces
  the budget itself rather than trusting the runtime). On success it makes a safety commit of
  anything uncommitted and fails the run if the diff since the run started is empty.
- **Verify.** Checks the card diff for size, protected paths, secret files and secret literals
  (catching anything that got past the policy some other way), then runs the gates in the
  worktree with a timeout, output written to `$PRAT_HOME/runs/run-<id>/gate-<name>.log`. A gate
  that times out or is cancelled is killed with every process it started (see "Process trees").
  Afterwards the worktree is reset so gate artefacts cannot be committed later.
- **Critique.** Calls the pluggable `Reviewer` with the diff, its stats, the diff summary, gate
  results, a read-only policy and the remaining budget, posts the verdict as a comment, and
  flags the run for rework on `not_met` or `wrong`. Without a reviewer, or with no budget left,
  the step is skipped.
- **Summarize.** Renders the summary from `git diff --numstat` and the commit list.
- **Finalize.** Safety commit, worktree removal, then status changes. Cleanup errors are
  recorded as warnings and never strand a card in `running`.
- **Cancel.** In the same process: sets a stop flag (which also kills a running gate), calls
  `runtime.cancel(session_id)` and cancels the task; the step records `cancelled` and finalize
  runs. Queued cards are cancelled before a run exists. Across processes: `prat card cancel`
  writes `cancel_requested_at`; the owning engine's heartbeat sees it and cancels.
- **Crash and resume.** Every running run has an owner id and a heartbeat. `recover()` marks
  runs whose heartbeat is older than `stale_run_seconds` as `interrupted` and puts their
  `running` steps back to `pending`. `resume(run_id)` re-enters the first unfinished step.
  Execute resumes the agent by session id when one was recorded. Finished steps are not
  repeated.
- **Approve.** Re-checks the size limits, merges with `--no-ff` in the agent clone, and on a
  conflict aborts the merge, leaves the card in review and posts the conflicting files as a
  comment.

## Process trees

`proc.py` starts every child process that PRAT may have to stop: gate commands, git, the fake
runtime's scripted commands and the Claude Code login probe. A gate is usually a tool that
starts other processes (a test runner and its workers), so stopping only the process PRAT
started would leave the rest running in the worktree.

- **Linux and macOS.** The child is started as the leader of a new session and process group,
  and the kill signals the whole group. The group is signalled only while its leader has not
  been reaped, so a process id that the system has reused is never signalled.
- **Windows.** The child is created suspended, placed in a Job Object and then resumed, so
  every process it starts belongs to the job from the first instruction; the kill terminates
  the job. If the job cannot be created or the process cannot be placed in it, the child still
  runs and the kill falls back to `taskkill /T /F` on its process id, then to killing the
  process itself.

`spawn` returns a `ProcessTree` (`kill`, `close`), and `run_tree` is the `subprocess.run`
equivalent with a timeout that kills the tree and does not wait on pipes a surviving process
might still hold. The tree is killed on a timeout or a cancel, not when a command ends
normally. Tests start a process that starts a grandchild, let the gate time out or cancel it,
and check that the grandchild has stopped writing.

## Journal

One file per run, `$PRAT_HOME/journal/run-<id>.jsonl`. Each line has `seq`, `ts` (UTC),
`kind` and `data`. Kinds include `run.started`, `run.resumed`, `step.started`, `step.finished`,
`execute.model`, `policy` (with `agent`: builder, planner or critic, and `loop`),
`runtime.<event>` (builder), `planner.<event>`, `critic.<event>`, `verify.finding` and
`verify.gate`. A `*.session` entry records the tool list the agent itself reported, which is
how a run proves the critic had no write or shell tools. Strings longer than
`journal_payload_limit` are trimmed with a marker. Every append is flushed and synced; the
reader skips a torn final line.

## Web UI

`prat serve` builds the same `Engine` as the CLI and hands it to `web.create_app`. uvicorn runs
the app on one asyncio loop, so `Engine.submit` schedules runs on the server's loop and the
limiter's semaphores govern runs from the browser and the CLI alike. Startup calls
`Engine.recover()`; shutdown cancels anything still scheduled. The Claude runtime is wrapped in
`DeferredRuntime`, which builds it on first use so the server starts (and the settings page can
explain the problem) even when authentication is not usable yet.

Every request passes `SecurityMiddleware` first (see docs/SECURITY.md for the rules). Pages are
server-rendered; actions are plain HTML forms that htmx upgrades, answered with a redirect (or
`HX-Redirect`) and a flash message stored in the session. Data access lives in `web/data.py`
and only reads; every change goes through `store` and `Engine`, so the state machines apply.

Live updates use server-sent events that poll rather than subscribe: the board stream watches
the newest `events` row and the step statuses of running runs; the card stream tails the run's
journal file (`journal.JournalTail`, which never returns a torn line) and watches a signature of
the card, its latest run, its steps and its comments. Polling the database and the journal means
changes made by the CLI or another process appear too. Journal entries are sent as HTML from the
same autoescaping template the page uses, with ids `<run>:<seq>` so a reconnecting browser
resumes where it stopped (`Last-Event-ID`) and the page skips duplicates. When the card's panel
needs to change (status, step, comment), the stream sends `status` and the page re-fetches the
panel; the card stream ends once the card stops running, and every stream ends after
`web_stream_seconds` (browsers reconnect).

Diffs are read at view time from the agent clone, from the run's base commit to the branch tip
(the run's recorded head commit once the card is merged), in a worker thread.

## Decisions and why

- **Clean rewrite with a fake runtime first.** Every lifecycle path (blocked, budget, cancel,
  crash, conflicts) is tested deterministically without a model, so the real adapter in Phase 2
  plugs into code that is already proven.
- **Structural push blocking.** String matching can always be evaded; a clone with no usable
  push destination cannot push, whatever runs inside it.
- **One policy function for all tool paths.** A safety rule that only covers the shell tool is
  bypassed by any other tool that runs commands or writes files.
- **Empty diff is failure.** An agent that reports success and changed nothing should not look
  like progress.
- **Summaries from the diff.** The review screen shows what changed, not what the agent claims.
- **Project file read from the default branch.** The agent cannot loosen its own rules: `.prat/`
  is always protected, and the committed copy is read before the agent starts. The installation
  ceiling (`max_autonomy`) caps whatever the file says.
- **Worktrees outside the clone.** Keeps `git status` in the agent clone clean and keeps PRAT's
  state in one place.
- **Synchronous SQLAlchemy, git in threads.** Database work is short; git and gate commands run
  through `asyncio.to_thread` so they never block the event loop.
- **Portable by construction.** pathlib everywhere, argument lists for subprocesses, generic SQL
  types, and a filesystem probe for case sensitivity. The one platform branch is in `proc.py`:
  a process group on POSIX and a Job Object on Windows, behind one interface.

- **Tool restriction by `tools`, not `allowed_tools`.** The SDK's `allowed_tools` approves; it
  does not restrict. Restricting the base set and listing the rest as disallowed means a
  reviewer that has no write tool cannot be talked into using one, and the hook still denies it
  if it somehow appears.
- **Fail closed.** A hook failure, an unknown tool, a tool outside the task's set or an
  unreadable reviewer answer never results in an action or an approval.
- **The engine owns budget and outcomes.** The SDK's own budget is passed through, but PRAT
  adds up cost itself and stops the session; outcomes are first-decided-wins.
- **Server-rendered UI with htmx, no build step.** One package to install; the security story
  (autoescaping, a strict CSP, no inline code) is easier to keep when the server owns the markup.
- **One middleware for every security check.** A per-route dependency is one forgotten line away
  from a hole; a middleware in front of the router fails closed for routes added later.
- **Polling streams.** A few cheap queries per second per open page keep the UI in step with the
  database and journals no matter which process changed them.

## Self-improvement

Off by default (`prat improve on`, or the toggle on the web page). While it is on, the
scheduler in `prat serve` runs a cycle every `interval_minutes`, one at a time:

```
sync the self clone -> diagnose (metrics, health, rules) -> roadmap -> research
  -> rank open findings -> file up to max_new_cards_per_cycle ready cards on prat-self
```

- **The self project** is PRAT's own repository, cloned to `$PRAT_HOME/self/repo` by
  `prat improve setup`. The clone is both checkout and agent clone and is hardened like every
  agent clone, so no agent session can push from it. PRAT never writes into the directory it
  runs from.
- **Findings** come from diagnostics (repeated errors, failing gates, reviewer rejections,
  frequent policy denials, loop trips, bad health checks), from `docs/ROADMAP.md`, and from
  research. Each has a stable fingerprint: a problem seen again updates its row, and a person's
  dismissal is never undone.
- **Ranking**: bad health and repeated errors, other diagnostics, the roadmap in file order,
  then research by severity and effort. Caps: the daily budget, open self cards, new cards per
  cycle.
- **Cards** carry the finding's acceptance criteria plus "the full test suite, lint and type
  check pass" and "new behaviour is covered by tests", the card budget, and an evidence comment.
  They run like any other card; the scheduler submits ready self cards while the toggle is on.
- **Approval is bound to a commit.** The verify step records the head commit with the gate
  results and the change classification; the critique step records the commit its verdict is
  for. `approve` refuses when the branch has moved since. Automatic approval also needs a `met`
  verdict and no category the project reserves for a person.
- Every decision is in `improve_events`; failures are in `error_log`.

### Landing self cards

Fully automatic: no class of change waits for a person. Proof replaces permission, and the
proof is produced by the running version, never by the candidate. Once a minute the scheduler
tends the self cards:

```
review -> sync with the remote -> build the exact --no-ff merge in a scratch worktree
       -> staged trial of that merge commit:
          checkout, install, selfcheck, trusted invariants, data, boot, probe, gates, teardown
       -> guard: land | refuse
land   -> engine merge (approve, automatic) -> real merge tree == proven tree -> push
refuse -> card can fix it: rework with the evidence; abandoned after max_reworks
          card cannot (remote diverged, trial postponed): hold and retry
failed or blocked self card -> rework with the reason, abandoned after max_reworks
```

- `improve/trial.py` runs the candidate on its own: the running interpreter with the
  candidate's source first (a new virtual environment only when dependencies changed), a copy
  of the database made with SQLite's online backup and migrated by the candidate (the running
  version must still read it), the candidate's server on a free loopback port with a scrubbed
  environment, and HTTP probes from the running PRAT. The whole process tree is stopped and the
  folder removed afterwards.
- `improve/guard.py` decides from evidence only: gates, both reviewers, the trial, all for the
  same commit, and splits refusals into rework and hold.
- `improve/landing.py` merges through the engine, checks the real merge has the proven tree
  (undoing it otherwise), and pushes through `$PRAT_HOME/self/publish.git`: fast-forward only,
  the default branch only, no tags, the self project only. A failed push is retried later. In
  `review` mode a proven card waits for a person, and a merge is pushed only if its tree was
  proven.
- The cycle revives failed findings after the cooldown and files a high-severity finding when
  the error a landed fix was meant to remove comes back, naming the commit to revert.

## Evaluation harness

`prat.evals` answers one question: does the review layer (gates, reviewers, rework) make what
PRAT hands over better than what the agent alone would have handed over? The guide covers
the commands and the case format; this is how it works.

- **Ground truth is a hidden check.** Each case carries a command and files the agent never
  sees. The check runs on a `git archive` of a commit with the hidden files laid over it,
  through `verification.run_command`, so it runs like a gate: in the sandbox when it is on,
  otherwise with `gate_environment()`.
- **One run, two answers.** The runner calls `Engine.run_card` until the card reaches review
  or the case runs out of attempts, sending it back with the gate output after a verification
  failure (the reviewer's rework happens by itself). After every run the hidden check judges
  the card branch's head in the agent clone, whatever PRAT decided. The first attempt is the
  baseline (the agent without the review layer); the last attempt, if it reached review, is
  PRAT's answer. Comparing the two within one run removes the noise of two separate agent
  runs.
- **Isolation by construction.** Each case gets its own home (`scratch_settings`: own
  SQLite database, no `database_url`, no project or fallback database server), a fresh
  repository and its own agent clone. Projects that declare databases are refused, so an
  evaluation cannot branch or write a real database.
- **Scripted and real.** The scripted runtime replays each case's `attempts/N.json` (chosen
  by the run's id), and `ScriptedReviewer` returns its `[[scripted_review]]` verdicts in
  order. Either side can be the real one: `--reviewer claude` judges fixed, known work, which
  measures the reviewer alone.
- **Plain Claude Code as the baseline.** `plain.run_plain` runs one Agent SDK session with
  Claude Code's own system prompt and every permission, the builder's model and budget, and
  the card's text; its result is committed and judged by the same hidden check. No policy,
  gates, reviewer or rework take part.
- **Real code, no history.** A case can name a repository and commit (`[source]`). The runner
  keeps a bare clone per repository and writes that commit's files from git's objects
  (`export_tree`, which also serves the hidden check), not with `git archive`, which leaves
  out files marked export-ignore. The agent's repository has one commit, so the fix cannot
  be found in the history.
- **Knowledge as an input.** `--knowledge` copies a project's knowledge folder into every
  case's home, so a seeded (and dreamed-over) knowledge base can be compared with none.
- **Replay of recorded cards.** `export_card` writes the base commit's files, the journals,
  each run's outcome, and one attempt per run: the builder's recorded shell commands as `dry`
  steps (the policy decides; nothing runs), then writes and deletes that reproduce the run's
  commit. The journal is trimmed and redacted, so file contents come from git, not from it.
- **Results are files.** `$PRAT_HOME/evals/<id>/result.json` holds every attempt (steps,
  gates, verdict, commit, hidden check, cost, policy decisions); summaries, step rates and
  comparisons are computed from it, so any two runs can be compared later.

## Known limits

- Without the sandbox (`sandbox = "off"`, the default) agents and gates run with the user's
  permissions and environment; with `sandbox = "docker"` (`prat.sandbox`) a card's shell
  commands, gates and environments run in containers (see docs/SECURITY.md). A gate's process tree is killed on a
  timeout or a cancel; a gate that ends normally and leaves a background process behind is not
  cleaned up, and the Claude Code process and its commands are stopped by the SDK, not by
  `proc.py`.
- The policy is defence in depth. Inline interpreter code and scripts allowed at `medium` can do
  anything the user can; structural controls (no push destination, and the sandbox's containers
  with no network and no inherited secrets) are what make that safe.
- Cancelling across processes relies on the heartbeat, so it takes up to `heartbeat_seconds`.
- Costs are estimates. Prices live in `model_registry.PRICES`; unknown models are priced as
  the most expensive known model.
- The planner and the critic do not resume after a crash; a resumed run re-enters the step and
  starts a new session for it.
- No MCP servers are configured for agents yet (the policy already judges registered MCP tools).
- Project knowledge, the scout and deep scan, and dreams exist (see the README and
  `docs/design/knowledge.md`); skills and the card drafter do not yet.
- Per-card data branches work for Dolt and Doltgres databases; plain Postgres or MySQL cannot
  branch.
- Web sessions are in the database and survive a restart, but the server is still one process:
  the engine, the sign-in throttle and flash messages live in it.

## Roadmap

1. Foundations.
2. Real agent: Claude Agent SDK adapter, hooks wired to `Policy.decide`, real
   reviewer and planner, budgets, loop detection, the BLOCKED protocol end to end, rework, and a
   run against a sample repository.
3. Web UI (this phase): board, card detail, live session, replay, review and approve, project
   setup, settings and health, authentication, CSRF and Origin checks.
4. Learning layer: project memory, skills with human-gated promotion, repository scout, card
   drafter, environment fingerprint.
5. Isolation and observability: container sandbox, network policy, secret store, OpenTelemetry.
6. Ecosystem and release: issue tracker integration, plugins, insights, docs site, packaging.
