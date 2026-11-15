---
name: ask-when-blocked
description: Use when the acceptance criteria are ambiguous, a credential or service is missing, the environment does not match the instructions, or the next step is a decision only the person should make. Explains when to stop and how to ask.
phase: any
tier: core
---

# Ask when blocked

Guessing wrong costs more than asking. But a vague question costs the person
time too. Investigate first, then ask one precise question.

## Stop and ask when

- **Ambiguous acceptance criteria.** Two reasonable readings would produce
  different code, and the card does not say which.
- **Missing credentials or services.** An API key, a third-party sandbox,
  a database, or a service the tests need is not available. Do not create
  fake credentials, do not edit `.env` files, do not hunt for keys elsewhere.
- **A decision that belongs to the person.** Product behaviour, pricing,
  wording shown to users, deleting data, adding a paid dependency, changing a
  public API contract, dropping support for something.
- **Environment drift.** The repo does not match the card (the file or
  feature it mentions does not exist, the branch state looks wrong, the
  runtime version differs from what the project requires) and the fix is not
  obviously yours to make.
- **The change grows far beyond the card.** A small fix turns out to need a
  large refactor or a migration on a big table.
- **Repeated failure.** You have tried a few distinct approaches, asked the
  `pair` helper, and are still stuck.

## Do not ask when

- The answer is in the code, the README, the card, or the tests. Look first,
  or ask the `scout` helper.
- It is a normal engineering choice inside the card's scope (naming,
  structure, which existing helper to use). Follow the codebase's conventions
  and mention the choice in the summary.
- A sensible default exists and is easy to change later. Pick it and say so.

## Before you ask

1. Do everything you can that does not depend on the answer. Commit that
   work.
2. Collect what you found: file paths, the exact error, the command you ran.
3. Work out the realistic options and what each would mean.

## How to write the question

One question. Concrete options. Your findings. Your recommendation if you
have one.

Good:

```
The card says "show the price with tax". Prices are stored without tax in
`products.price` and tax rates live per region in `tax_rates`, but the
product page has no region context.

Options:
A) Use the store's default region rate (simple, wrong for other regions).
B) Add a region selector to the product page (larger change, new UI).
C) Show the price without tax plus a "tax calculated at checkout" note.

I recommend A for now. Done so far: price formatting helper and tests,
committed.

BLOCKED: Which option should the product page use for tax: A, B or C?
```

Bad: "What should I do about tax?" or a list of five unrelated questions.

## PRAT protocol

- End your final message with exactly one line starting with `BLOCKED: `
  followed by the question. Nothing after that line.
- Commit any useful completed work first, so it is not lost.
- Do not push, and do not work around the block by guessing in code.
- Leave the worktree clean: no half-applied changes left uncommitted.

## Common traps

- Asking before reading the code that answers the question.
- Hiding a block inside a long summary instead of using the `BLOCKED:` line.
- Asking several questions at once. Pick the one that unblocks the most.
- Silently choosing an option for a decision that belongs to the person.
- Inventing placeholder credentials or mocking an external service in
  production code to "get past" a missing dependency.
