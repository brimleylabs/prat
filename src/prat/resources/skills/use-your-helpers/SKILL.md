---
name: use-your-helpers
description: Use at the start of any non-trivial card and whenever you are about to read many files, debug a failing test, query the database, check a running page, or feel stuck. Explains PRAT's helper subagents and how to brief them.
phase: any
tier: core
---

# Use your helpers

PRAT gives you helper subagents. They save your context and time when the
question is clear. You stay responsible for the work: helpers answer
questions, you make the edits.

## The helpers

| Helper | Use it for | It returns |
| --- | --- | --- |
| `scout` | Read-only codebase questions: where is X defined, what calls Y, how is Z usually done here | File paths, line references, a short answer |
| `tester` | Running a failing test or gate and diagnosing it | The cause and the next step to try |
| `pair` | A second opinion when stuck or choosing between approaches | A recommendation and reasoning |
| `data-analyst` | Read-only SQL on the card's database branch: row counts, data shape, checking a migration or backfill | Query results and what they mean |
| `web-checker` | Fetching pages of the running preview and checking status, assets and content | Pass or fail per page, with details |

## When to call them

- **Early.** At the start of a card, send `scout` to map the relevant code
  while you read the card and plan.
- **In parallel** when the questions are independent: for example `scout`
  for "where is auth middleware registered" and `data-analyst` for "how many
  users have a null email" at the same time.
- **`tester`** as soon as a test fails in a way that is not obvious from the
  first error, instead of re-running it in a loop yourself.
- **`pair`** after two distinct attempts at a problem have failed, or before
  a design choice that would be costly to reverse.
- **`data-analyst`** before and after any data change (see
  `safe-database-changes`).
- **`web-checker`** after a UI or route change, once the preview is running
  (see `web-ui-checks`).

## How to brief a helper

A helper knows only what you write. Make the request self-contained:

- The goal in one sentence.
- What you already know or ruled out.
- Exact names: files, functions, routes, tables, test ids.
- What shape of answer you want.

Good:

```
scout: In this repo, where are HTTP routes for the admin area registered,
and which middleware applies to them? I already checked routes/web.php and
found only public routes. Return file paths with line numbers and the
middleware names.
```

```
tester: `pytest tests/test_invoices.py::test_totals_with_discount` fails
with AssertionError 90 != 95 after my change to invoices/totals.py
(discount now applied before tax). Find whether the code or the test
expectation is wrong. Return the cause and the fix you suggest.
```

Bad: "look at the code and tell me what is wrong", "check the database".

## Do not

- Delegate the actual editing. You write and commit the code.
- Ask a helper something you could answer with one `grep` or one file read.
- Re-do a helper's work yourself in parallel. Wait for the answer.
- Accept an answer blindly. Spot-check the file paths and claims it returns
  before building on them.
- Send secrets, `.env` contents or credentials to any helper.

## Using the answers

- Copy the key facts (paths, causes, counts) into your notes or task list so
  they survive a long session.
- If a helper's answer contradicts what you see, trust the code, and
  re-ask with the specific contradiction.
- Mention helper findings that matter in your final summary, with evidence
  ("data-analyst: 1,204 rows backfilled, 0 remaining NULL").
