---
name: report-progress
description: Use at the start of every multi-step card and throughout it: write the plan as tasks, keep them current, call report_progress during long work, and use start_job for long-running data jobs instead of shell loops.
phase: any
tier: core
---

# Report progress

The person watching a card should be able to see what you are doing, what is
done, and what is left, without reading your whole transcript.

## Task list

At the start, after reading the card and the relevant code, write the plan as
tasks:

- One task per meaningful step, usually 3 to 8 tasks.
- Phrase each as an outcome: "Add migration for `orders.status_v2`",
  "Backfill status_v2 in batches", "Update API serializer", "Run tests and
  lint", "Verify acceptance criteria".
- Always include a verify task near the end (see `verify-before-finishing`).

As you work:

- Mark exactly one task in progress at a time.
- Mark a task done as soon as it is done, not in a batch at the end.
- If the plan changes, update the list: add the new task, remove or rename
  stale ones. A stale list is worse than none.
- If a task is blocked, say so in the task and follow `ask-when-blocked`.

## report_progress

Call PRAT's `report_progress` tool when work is long or has several phases:

- When you finish a phase ("Migration written and round-tripped, starting
  backfill").
- When something important changes ("Found 3 callers of the old API, not
  1; updating all").
- Roughly every few minutes of continuous work if nothing else prompted it.

Keep each report to one or two sentences, factual, with numbers where they
exist. No filler like "making good progress".

## Long-running jobs

For anything that takes more than a couple of minutes (data imports,
backfills, reindexing, large test matrices, scraping, model downloads):

- Start it with PRAT's `start_job` tool. Do not run it in the foreground and
  do not write `while` / `sleep` loops in the shell to poll it.
- Make the job itself log progress and be restartable (batches, checkpoints,
  idempotent steps).
- Do not babysit it. Continue with independent tasks, or finish your other
  work and report that the job is running.
- When the job completes, check its result with evidence (row counts, exit
  status, log tail) before marking the task done.

Bad:

```bash
python scripts/backfill.py &
while true; do tail -1 backfill.log; sleep 10; done
```

Good: start `python scripts/backfill.py --batch-size 5000` with `start_job`,
note it in `report_progress`, move on to the serializer change.

## Final summary

The task list and the summary should agree. Every task is done, explicitly
dropped with a reason, or blocked with a `BLOCKED:` line. Mention any job
still running and how to check it.

## Common traps

- Writing the task list once and never touching it again.
- Ten tiny tasks for a two-line fix. Small cards need two or three tasks.
- Reporting intentions instead of results.
- Polling a job in a tight loop and burning the session on it.
- Marking a task done before its check actually ran.
