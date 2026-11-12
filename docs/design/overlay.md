# The overlay: improving an installation without changing PRAT

## Why

PRAT improves itself. On PRAT's own repository that means changing PRAT's source. On anyone
else's installation it must not: an installation that edits its own copy of PRAT becomes a fork,
and the next release either overwrites its improvements or conflicts with them.

So PRAT has two layers:

- **The core** is PRAT as released: code, built-in rules, prompts and the safety model. It
  changes only through releases.
- **The overlay** is one installation's own improvements: extra rules, guidance, settings,
  checks and skills, kept in `$PRAT_HOME/overlay/` in its own git repository. PRAT reads it at
  start. Upgrading PRAT never touches it.

Self-improvement on an installation writes to the overlay. Changes that really need the core
become proposals for upstream, never local edits.

## Principles

1. **The core is not editable by the loop.** Outside PRAT's own repository, self-improvement can
   only change files in the overlay.
2. **The overlay can add and tighten, never loosen.** It cannot remove a denied path, the push
   block, an invariant, a reviewer, a gate or a person's approval. This is enforced where the
   overlay is applied, not by convention: core rules are applied last and win.
3. **A versioned contract.** The overlay declares `overlay_api = 1`. PRAT states which versions it
   reads. An item PRAT no longer understands is switched off with a notice, never a crash.
4. **Every change is reviewable and undoable.** The overlay is a git repository: each change is
   a commit with its proof, shown in the UI, reverted with one action.
5. **Portable.** An overlay, or part of it, can be exported and imported. Shared overlays are
   rule packs.

## What the overlay holds (overlay_api 1)

```
overlay/
  overlay.toml          overlay_api, name, description
  settings.toml         installation settings the loop may tune, within bounds
  rules/*.toml          denied commands and paths, categories that need a person,
                        change-classifier rules, tighter loop and diff limits
  prompts/<role>.md     extra guidance for builder, planner, critic, security reviewer,
                        researcher; appended, fenced, cannot replace PRAT's own text
  checks/*.toml         review criteria added to every card, or to cards of one stack
  skills/*.md           task playbooks agents load when their description matches
```

Precedence for settings: core defaults, then the overlay, then `config.toml` and `PRAT_*`
variables (the person's explicit choices win over the loop's tuning), then core limits, which
nothing overrides. Project files keep working as they do; the overlay applies across projects.

Later versions add `plugins/`: Python entry points on a stable hook API (classify a change,
contribute review criteria, react to a landed card), versioned like the rest.

## Self-improvement with the overlay

`improve.target` is `overlay` (the default) or `core` (PRAT's own repository, for maintainers).

With `overlay`, a cycle files cards against the overlay repository. The builder may write only
there. The proof before a change lands:

1. The overlay validates against its schema and the "never loosen" rules.
2. PRAT's invariant suite passes with the overlay applied.
3. A replay evaluation shows no regression on recorded cards (the evaluation harness).
4. Both reviewers pass it.

Landing is a commit in the overlay repository. Undo is a revert. Nothing is pushed anywhere
unless the person sets a remote for their overlay.

A finding that cannot be met in the overlay (it needs new core code) becomes an upstream
proposal: an issue text and, where there is one, a patch against the matching PRAT release, for
the person to send if they choose.

## Upgrades

On start PRAT reads `overlay_api` and each item. Items it understands apply; items it does not
are listed on the Improve page as switched off, with the reason. Release notes say which overlay
items a release changes. A release may add overlay features freely; removing or changing one
raises `overlay_api`, and PRAT keeps reading the previous version for at least one release.

## Phases

1. (Done.) Overlay loader and schema; rules, prompts and settings applied; the "never loosen" checks with
   tests; `prat overlay` commands (show, validate, log, undo); an Overlay section on the Improve
   page.
2. Self-improvement targets the overlay by default, with its proof. Done: `improve.target` (`overlay`
   by default, `core` for maintainers), the overlay's own project file and validation gate, an
   overlay prover, and landing that fast-forwards the live overlay, never forced.
3. Skills and checks; export and import; upstream proposals.
4. Plugin hooks.
