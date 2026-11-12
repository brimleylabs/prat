# PRAT security model

This describes what PRAT protects against today (Phase 3), how, and what it does not yet
protect against. PRAT is pre-release; do not point it at repositories or machines where an
agent with your user's permissions could do damage you cannot undo.

## What PRAT is defending

- **Your own checkout** of each repository. No agent runs there and PRAT never writes there.
- **The remote** behind your checkout. Nothing PRAT runs should be able to push.
- **Your machine and accounts**: files outside the card's worktree, credentials, cloud and
  deployment tools, system configuration.
- **Your PRAT installation**: its database, journals, configuration and other projects.
- **Your money and plan limits**: runaway sessions.
- **Your review**: you should see what really changed, judged on evidence.

## Threats considered

1. The model makes a mistake (wrong file, destructive command, endless loop).
2. The model is steered by content it reads (prompt injection in the repository, in issue
   text, in tool output) into doing something you did not ask for.
3. A project file in a repository tries to loosen PRAT's rules.
4. The builder reports success it did not achieve.

PRAT does not yet defend against a determined attacker who controls the repository and targets
PRAT specifically (see "Not covered yet").

## Controls in place

**Structural (cannot be talked around):**

- **Separate agent clone.** PRAT works in its own clone of each repository and creates one git
  worktree per card outside it. Registration refuses overlapping paths, and every worktree path
  is checked against every registered checkout before a run.
- **Push is impossible from the agent clone.** The push URL is a scheme with no transport, every
  push destination is rewritten to it, and a pre-push hook refuses as a last layer. Tested with
  real `git push` variants, including `--no-verify` and explicit paths.
- **Approval merges locally.** `prat card approve` makes a `--no-ff` merge in the agent clone.
  Pulling it into your checkout, and pushing, are your actions.
- **Restricted tool set.** The Claude runtime sets the SDK's `tools` to exactly the tools a role
  needs and lists every other built-in tool as disallowed. The reviewer and planner get Read,
  Glob and Grep only; their sessions report this tool list, and the journal records it.
- **Web tools for the researcher only.** Web search and fetch are not part of any builder,
  reviewer or planner tool set. Only the self-improvement researcher, which is read-only, is
  given them. Three layers enforce this: the tools are offered to the model only when the task's
  tool set includes them, the hook refuses any tool outside that set, and the policy denies web
  calls unless it was built with web access (and then allows a fetch only for http or https).
- **Settings isolation.** Agent sessions do not load your Claude Code user or project settings
  (hooks, permission rules, plugins, memory files) or MCP servers from your configuration. The
  one thing PRAT passes on from a repository is the text of its `AGENTS.md` and `CLAUDE.md`,
  which PRAT reads itself (see "Repository instruction files").

**Policy (every tool call, defence in depth):**

- One function, `Policy.decide`, judges shell commands, file reads and writes and MCP tool
  calls. The Claude runtime calls it from a `PreToolUse` hook for every tool, MCP included; the
  hook fails closed, and a fallback permission callback denies anything the hook did not
  approve. Denials are journaled with their reason and returned to the model.
- Autonomy levels (read_only, low, medium, high), capped by the installation's `max_autonomy`;
  a project file or card can only lower them.
- Always denied: any form of git push, remote and config changes, worktree management, branch
  deletion, history rewriting, privilege escalation, cloud and deployment CLIs, publishing,
  system administration, `eval`, shells reading commands from standard input, `GIT_DIR`-style
  variables, writes outside the worktree, writes to `.git`, `.prat/` and protected paths, reads
  and writes of secret files, and any write or command containing a credential-shaped literal.
- Read-only tools stay read-only. A `sed` script that runs a command or reads or writes another
  file (`e`, `r`, `R`, `w`, `W`, `s///e`, `s///w`) is denied at every level, and so is one read
  from a file. Output options (`sort -o`, `git log --output`, `tree -o`, a second operand to
  `uniq` or `xxd`, `yq -i`) are judged as writes, and options that run a program (`rg --pre`,
  `fd --exec`, `sort --compress-program`, `git grep -O`) as running it. `awk` needs `medium`.
  sed options must be spelled out in full (GNU sed's abbreviations such as `--expr` are
  refused) and option values such as `-l 5` are read as values, so every script is seen.
- Variables that make a later command run other code (`PATH`, `LESSOPEN`, `PAGER`,
  `GIT_PAGER`, `EDITOR`, `LD_PRELOAD`, `PYTHONSTARTUP`, `NODE_OPTIONS` and the like) need
  `medium`, however they are set. A path in a variable is judged where it is assigned.
- Paths are judged after the shell would expand them: wildcards and braces against the files
  that exist, and `$HOME`-style variables; a path built from any other variable is refused.
  A recursive search (`grep -r`, `rg`, `find -exec`) of a folder that holds a credential store
  is refused, and so is any reference to a variable whose name looks like a credential.
- Credential stores are secret wherever they are: `~/.ssh`, `~/.aws`, `~/.config/gh`, gcloud,
  Azure, Docker and Kubernetes configuration, `~/.git-credentials`, `~/.claude.json`, `.pgpass`,
  `/proc/*/environ` and similar, besides the secret file names. Printing the whole environment
  (`env`, `printenv`, `export -p`) or a variable whose name looks like a credential is denied.
- Background shell commands are denied (they would outlive the run).
- `.prat/project.toml` is read from the committed default branch before the agent starts and is
  always protected, so an agent cannot rewrite its own rules for the current or a later run.
- **Loop detection**: the same call 5 times in 8 is denied with an instruction to stop.

**Verification and review:**

- Empty diffs fail. Oversized diffs, protected-path changes, secret files and credential-shaped
  literals in the diff fail verification, whatever route produced them.
- The project's gates (test, lint, build) run in the worktree with a timeout, and must pass.
  A gate that times out or is cancelled is killed together with every process it started, on
  Linux and macOS through its process group and on Windows through a Job Object.
- The summary on the card is computed from the diff. The reviewer is a separate session that
  sees the diff and gate output, not the builder's account, and cannot write. A reviewer that
  fails or answers unreadably never approves; it hands the card to a person.
- **Approval is bound to a commit.** The gate results, the change classification and the
  verdict each record the commit they were computed for. Approving refuses when the card branch
  has moved since, so a commit added after review cannot ride along.
- **Security reviewer.** A second, independent read-only session, on for projects that ask for
  it and always on for PRAT's own repository. It looks only for weakened isolation, policy,
  authentication, verification or push rules and for unsafe handling of untrusted input, and
  answers pass or concern. A concern, an unreadable answer, a session that does not finish, or
  no security reviewer at all counts as a concern: the card goes back with the findings, and
  nothing is approved automatically without a pass for the exact commit.
- **Invariant suite.** `tests/invariants/` states what must stay true. When PRAT judges a
  change to itself, the suite from before the change is exported from git and run outside the
  candidate's tree with `python -I`, a fixed pytest configuration and plugin autoloading off, so
  editing an invariant, adding a conftest or registering a plugin cannot weaken it. Loosening
  an invariant takes a commit by a person.

**Self-improvement (off by default):**

- Research output from the web is data. It must match a strict schema (extra fields forbidden,
  every field capped, sources limited to http and https URLs) or it is rejected whole. A
  proposal that asks to weaken a protection, add credentials, let agents push or force a push
  is dismissed by a keyword screen, and the researcher's prompt tells it to ignore and report
  instructions found in pages.
- Setup refuses a repository URL with a password in it (credentials belong to git's credential
  helper) and a PRAT home inside PRAT's own checkout.
- Turning it off stops scheduling, cancels a research session in flight, and stops filing,
  running and landing self cards.
- Nothing lands unproven: the exact merge commit passes the trusted invariant suite, the staged
  trial (boot on a spare loopback port, sessions, CSRF, Origin, a scripted card whose push and
  protected-path write must be denied) and the project's gates, and both reviewers passed the
  card's head. The real merge must have the proven tree or it is undone.
- A trial never touches the live database, home or ports. The candidate runs with a scrubbed
  environment (no `PRAT_*`, `ANTHROPIC_*` or `PYTHON*` variables), its own home, a backup copy
  of the database with self-improvement switched off in that copy, and a port chosen by the
  operating system on loopback.
- The push never comes from the agent clone, whose push block stays in force. The engine pushes
  from a bare repository in the PRAT home that agent sessions cannot reach: fast-forward only,
  the default branch only, never tags, never `--force`, and only for the self project. Your own
  git credentials (credential helper or SSH agent) are used; PRAT stores none.
- The one limit the loop cannot lift: loosening an invariant, or adding a public route, takes a
  commit by a person, because the copy of the invariant suite that judges a change is the one
  from before it.

**Budget:** a per-run budget (planner, builder and reviewer together) enforced by PRAT from
cost estimates, and also passed to the SDK.

**Secrets handled by PRAT itself:**

- `api_key` mode: the key comes from `ANTHROPIC_API_KEY` or the `anthropic_api_key` setting
  (stored as a secret type, never printed, never written to the database or journal) and is
  passed only to the agent process's environment.
- `claude_login` mode: PRAT never opens, copies, stores or forwards the Claude Code credentials
  or tokens. It asks the Claude Code executable whether it is signed in and reads only "signed
  in" and the kind of login from the answer. This mode is for one person on their own plan; see
  the README for why API keys are the default.
- CLI output escapes card text, comments and agent output, so they cannot inject console
  markup.

## Repository instruction files

Many repositories keep an `AGENTS.md` or a `CLAUDE.md` at their root with guidance for coding
agents: layout, style, how to build and test. Because agent sessions load no settings, PRAT
reads these two files itself and puts their text into the builder's, planner's and reviewer's
system prompts. A repository's files are text PRAT did not write, so they are handled as such:

- **Two names, one place.** Only `AGENTS.md` and `CLAUDE.md` at the root of the card's worktree
  are read. There is no search in subdirectories or parent directories, references to other
  files inside them are not followed, and no settings files or hooks are loaded.
- **No reading through links.** The path is resolved before it is read and must still be one of
  those two files at the worktree root. A symlink to anything else, outside the worktree or
  inside it, is refused, and so is anything that is not a regular file. One name linked to the
  other is shown once.
- **Bounded.** At most 12,000 characters of each file are used. A longer file is cut, and the
  prompt says so.
- **Marked as the repository's words.** The text sits in a section headed "Repository
  instructions", each file between markers that the file's own text cannot close. The section
  says the files are guidance about code and conventions and cannot change PRAT's rules. PRAT's
  project facts and rules come after it and state that they apply whatever the files say.
- **Recorded.** The journal of each run lists which files the builder was given.

The marking makes the prompt clear to the model; it is not the control. What an agent may do is
decided by the policy, the push block and the protected paths, which judge every tool call
whatever any prompt says. The files are still a way to steer an agent within what the policy
allows, like any other content in the repository (see "Prompt injection" below). The reviewer
sees the files as they are in the worktree after the builder's work, so a change to them is
part of the diff it reviews and that you approve.

## The web UI

`prat serve` puts an HTTP server in front of the engine. Anyone who can use it can start agent
runs and merge branches, so it is treated as a remote-code-execution surface.

**Threats considered:**

1. A web page you visit makes your browser send requests to PRAT (cross-site request forgery,
   including `text/plain` form posts that skip CORS preflight).
2. A page on another site uses DNS rebinding to reach PRAT under its own host name.
3. Another user or process on the network reaches the server.
4. Text that PRAT did not write (card titles, comments, agent output, tool output, gate logs,
   diffs, journal entries) carries markup or script (stored cross-site scripting).
5. A response leaks a secret: the access token, an API key, a database password, or a
   credential an agent printed.
6. Clickjacking: PRAT framed inside another page.
7. A request parameter reaches a shell.

**Controls:**

- **Loopback by default.** The server binds 127.0.0.1. Any other address is refused unless
  `web_allow_remote = true` or `--allow-remote` is given, which prints a warning; a wildcard
  address also needs `web_public_url`. Put TLS in front of a remote server; with an `https`
  public URL the session cookie is marked `Secure`.
- **Authentication.** `prat init` writes a random 256-bit access token to
  `$PRAT_HOME/access-token`, created with mode 0600 where the system supports POSIX modes (on
  Windows the file inherits the PRAT home's permissions; keep that directory private). The token
  is never sent in a response or a cookie. Signing in exchanges the token, or a one-time login
  code derived from it (HMAC-SHA256, ten-minute expiry, single use), for a server-side session.
  The session cookie holds only a random id and is `HttpOnly`, `SameSite=Strict` and `Path=/`.
  Sessions are rows in PRAT's database, stored under a SHA-256 hash of that id, so a copy of the
  database does not contain a usable cookie, and signing in survives a restart of the server.
  Used login codes are recorded the same way, so a code works once even across a restart.
  Sessions expire after `web_session_hours`, on logout, and when the token is rotated
  (`prat rotate-token`). Ten failed sign-ins within a minute block further attempts for
  that minute. Every route except `/login` and `/static/` requires a session.
- **CSRF.** Every state-changing request (anything but GET, HEAD and OPTIONS) must carry the
  session's CSRF token in the `X-CSRF-Token` header (htmx adds it) or a `csrf_token` form field,
  compared in constant time. The `Origin` header (or, when a browser omits it, the `Referer`)
  must be one of the server's own origins, and requests with neither are refused. The body
  must be empty or `application/x-www-form-urlencoded` (1 MiB at most), so a cross-site
  `text/plain` post fails even before the token check. `POST /login` has no session yet and gets
  the Origin and body checks. The checks live in one middleware in front of every route, and a
  test posts to every state-changing route without a token and expects a refusal.
- **Host check.** Requests whose `Host` header does not name this server are refused, which
  defeats DNS rebinding.
- **Escaping.** Templates are Jinja2 with autoescaping always on and strict undefined variables.
  No template uses `|safe`; the only markup built in Python is the constant SVG icon set. Nothing
  is rendered as Markdown: text is shown as text with its line breaks. Live journal entries are
  rendered on the server by the same autoescaping template and inserted as received. Tests put
  a script payload in a card title, a comment, agent text and a file in the diff, and check every
  page, the replay and the live stream.
- **Content Security Policy.** `default-src 'none'; script-src 'self'; style-src 'self';
  img-src 'self'; connect-src 'self'; font-src 'self'; form-action 'self';
  frame-ancestors 'none'; base-uri 'none'`. There are no inline scripts, inline styles or event
  handler attributes anywhere (a test checks every page), htmx runs with `allowEval` and
  `allowScriptTags` off and `selfRequestsOnly` on. Also sent: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy` and
  `Cross-Origin-Resource-Policy: same-origin`, and `Cache-Control: no-store` on everything but
  static files.
- **No secrets in responses.** The settings page shows the authentication mode and whether it is
  usable, never the key; the database URL is shown with its password hidden; the token is never
  displayed. Agent, tool and gate output is passed through the credential detector before display
  and matches are masked. A test configures a key and checks every page.
- **No shell.** Every command the UI triggers (git, gates, the agent) runs from an argument list.
  The UI has no way to edit commands: project settings are read-only and come from the committed
  project file. Card actions go through the engine and the card state machine, which reject
  illegal transitions.
- **No third-party code at run time.** htmx is vendored at a pinned version; there are no CDNs
  and no web fonts.

## The sandbox

With `sandbox = "docker"` in `config.toml` (macOS and Linux, after `prat sandbox build`),
everything that runs a project's code for a card runs in a fresh container: the agent's shell
commands, the gates, the setup checks and the project's environments. Each container:

- mounts only the card's worktree, read-write, and the clone's git directory, read-only, at the
  same paths as on this machine, plus a home folder of its own for caches. The worktree's own
  `.git` (the file that tells git where the repository is) is mounted read-only on top;
- sees none of your files outside those mounts and none of your environment variables, so no
  SSH keys, cloud credentials, GitHub CLI login or API keys;
- runs as your user id with every capability dropped, no new privileges, and limits on memory,
  CPU and processes (`sandbox_memory`, `sandbox_cpus`);
- has no network. A project database on this machine is reached through a relay container on a
  private network with no route out, and only the ports the card's own variables name;
- is removed when its command ends, times out or is cancelled; whatever a card left behind is
  removed when its step ends.

The agent's plain `git` commands stay on this machine, where the policy judges them, so a
container never writes the git directory. A container can still write anything else in the
worktree, so git on this machine treats the worktree as hostile:

- the agent's git commands name the card's git directory outright (`GIT_DIR`, `GIT_WORK_TREE`),
  so a `.git` folder planted in a subfolder is never found;
- every git command PRAT or the agent runs here switches off settings that run a program or
  look inside submodules (`core.fsmonitor`, submodule recursion), ahead of any config file;
- after each step that ran containers, PRAT checks that the `.git` file is unchanged and that
  no `.git` appeared anywhere below the worktree's top; if one did, the run fails. The setup card installs
dependencies, so its containers have the network; so do environments, which the browser must
reach. A project chooses its image with `[sandbox] image = "..."` in its project file; it needs
`sh`, and `socat` for environments. If the sandbox is configured but cannot run, the card fails:
PRAT never falls back to running the commands on this machine.

## Not covered yet

- **Without the sandbox, nothing is confined.** With `sandbox = "off"` (the default) agents and
  gate commands run as your user, with your environment variables, your network access and
  your file permissions. The policy is analysis of commands, not confinement. At `medium` and
  above an agent may run scripts and inline interpreter code, which can do anything you can.
  At `low` it may edit files and run the project's own commands, and those commands run code
  in the worktree: an agent that writes a `conftest.py` and then runs the tests has run code of
  its choosing. So `low` limits what an agent asks for directly; it does not contain the
  repository's code. For a repository you do not trust, turn the sandbox on (see "The
  sandbox"), or keep autonomy at `read_only`.
- **Environment variables are inherited** without the sandbox, including any credentials in
  your shell, except PRAT's own: gates get no `PRAT_*`, `ANTHROPIC_*` or `CLAUDE_CODE_*`
  variables, and the agent's `PRAT_*` variables are blanked. With the sandbox, containers get
  only the variables PRAT passes.
- **The policy is guardrails, not a boundary.** It analyses commands before they run and stops
  a cooperative agent's mistakes and the obvious misuses, but a list of rules about a shell
  cannot be complete: reviews keep finding routes it does not know. For code you do not trust,
  the boundary is the sandbox.
- **What the sandbox leaves on this machine.** The agent's file tools (read, write, edit,
  search) and git commands it runs on their own are judged by the policy and run here, as does
  the Claude Code process, which needs the network to reach the model. The setup card's
  containers and environments have the network, and through the container gateway can reach
  services listening on this machine. The sandbox runs on macOS and Linux only, and its
  strength is Docker's.
- **Database branches are a workspace, not a wall.** A card's agent gets the project database's
  own credentials, pointed at the card's branch. Those credentials can reach the default branch
  too, so an agent able to run SQL (or code that does) could change it without review. Use
  credentials limited to what the agent should touch, and point PRAT at a copy of any data you
  cannot afford to lose; at `low` the project's own commands, run on code the agent can edit,
  can reach it too. Per-card database users confined with Dolt's branch permissions are planned.
- **Reads outside the worktree** are allowed except under denied roots (your checkouts, agent
  clones, the PRAT home) and secret file names. An agent can read other files your user can
  read, including files in your home directory that do not look like secrets.
- **Directory-wide search** (Grep or `grep -r` over a directory) is judged by its root, so it may
  read secret-looking files inside the worktree that a direct read would be denied. Most such
  files are git-ignored, which the search tools respect by default, but this is not enforced.
- **Network** is not restricted for Claude Code's own API traffic, and commands classed as
  network tools are allowed at autonomy `high`.
- **Prompt injection** can still steer the agent within what the policy allows, through any
  content it reads, the repository's instruction files included. The reviewer is a second
  opinion, not a guarantee; you review and approve every merge.
- **The CLI trusts whoever runs it**, and anyone who can read the PRAT home can sign in to the
  web UI (the access token lives there). That is the same boundary as the database and journals.
- **The web UI has one identity.** There are no user accounts or roles; everyone who signs in can
  do everything. A running server does not support several worker processes: the run engine,
  the sign-in throttle and the notices shown after an action live in the one server process,
  and the throttle starts again from zero after a restart.
- **No rate limiting beyond sign-in.** A signed-in client can queue as many runs as it likes; the
  concurrency limits and budgets still apply.
- **Remote access** relies on you adding TLS and keeping the token private. PRAT itself serves
  plain HTTP.
- **Single user.** One installation is one person. Do not share an installation, an API key or
  a Claude login between people.
- **Processes that outlive a gate.** The process tree is killed when a gate times out or is
  cancelled. A gate that ends normally but leaves a background process behind is not cleaned
  up, and neither is a process that deliberately moves out of its process group on Linux or
  macOS. The Claude Code process and the commands it runs are started and stopped by the SDK,
  not by PRAT's own process handling.
- **Journals** may contain trimmed tool output and file content the agent read. They live in
  the PRAT home with your user's file permissions.

## Reporting a problem

Until there is a public repository and a security contact, report issues privately to the
maintainer rather than in a public tracker.
