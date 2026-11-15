---
name: verify-before-finishing
description: Use before you commit and declare a card done, whenever you think the work is finished. Runs the project's tests, lint and build, checks the diff for stray files, and confirms every acceptance criterion with evidence.
phase: verify
tier: core
---

# Verify before finishing

"It should work" is not evidence. Finish a card only after you have run the
project's own checks and can point to proof for every acceptance criterion.

## 1. Find the project's real commands

Look, in this order, and use what the project already defines:

- The card or project instructions (any commands listed there win).
- `package.json` scripts, `Makefile`, `justfile`, `composer.json` scripts,
  `pyproject.toml` / `tox.ini` / `noxfile.py`, `go.mod` with `go test ./...`.
- CI config (`.github/workflows/*.yml` and similar): it shows exactly what
  must pass upstream.

Do not invent a new test runner or install global tools to get a green run.

## 2. Run them, narrow first, then wide

1. Run the tests closest to your change (a single file or test name).
2. Run the full test suite if it is reasonably fast.
3. Run lint and type checks (`tsc --noEmit`, `ruff`, `mypy`, `phpstan`,
   `go vet`, whatever the project uses).
4. Run the build if the project has one.

## 3. Read failures properly

- Read the first failure, not the last line. The root cause is usually the
  first stack trace or the first compiler error.
- Decide whether the failure is yours: run the same test on the default
  branch state (for example `git stash` then re-run, then `git stash pop`) if
  you are unsure. Pre-existing failures are reported, not silently fixed or
  hidden.
- Never make a test pass by deleting it, skipping it, loosening an assertion
  to meaningless, or catching and swallowing the error.
- After a fix, re-run the exact command that failed, then the wider suite.
- A flaky test gets one re-run. If it flips again, say so in the summary.

## 4. Inspect the diff

```bash
git status --short
git diff --stat
git diff
```

Check for and remove:

- Build output, caches, coverage reports (`dist/`, `build/`, `__pycache__/`,
  `.pytest_cache/`, `coverage/`, `*.log`).
- Lockfile churn you did not intend. A lockfile should change only if you
  changed dependencies on purpose.
- Debug prints and leftovers: `console.log`, `print(`, `dd(`, `var_dump`,
  `fmt.Println` used for debugging, `debugger`, commented-out code blocks.
- Unrelated formatting changes across files you did not need to touch.
- Any `.env` or secret-bearing file. These must never be in your diff.
- Temporary scripts and scratch files you created along the way.

Every changed file should be explainable by the card.

## 5. Check each acceptance criterion

Write a short list, one line per criterion, with the evidence:

- a test name that covers it and passed,
- a command and its relevant output,
- an HTTP check against the running preview (see `web-ui-checks`),
- a query result on the card's database branch.

If a criterion cannot be verified in this environment, say exactly why and
what a person should check. If a criterion is ambiguous, see `ask-when-blocked`.

## 6. Commit

- Stage files explicitly (`git add path/one path/two`), not `git add -A`
  blindly.
- One clear message: what changed and why, in the imperative
  ("Add rate limit to login endpoint").
- Do not push. Do not amend or rewrite commits that were not yours.

## 7. Final summary

End with a short summary the reviewer can trust:

```
Changed: <files or areas, one line each>
Checks run: <command> -> <result>, <command> -> <result>
Acceptance criteria:
- <criterion 1>: met, <evidence>
- <criterion 2>: met, <evidence>
Not verified / follow-ups: <anything left, or "none">
```

## Common traps

- Running tests from the wrong directory or with the wrong interpreter, so
  nothing actually ran. Check the test count in the output.
- "0 tests collected" counted as a pass.
- Fixing the symptom in a test fixture instead of the code.
- Claiming a UI works without loading the page.
- Leaving the worktree with uncommitted changes at the end.
