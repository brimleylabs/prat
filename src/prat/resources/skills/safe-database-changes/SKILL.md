---
name: safe-database-changes
description: Use whenever you add or change a database migration, schema, seeder, or run any statement that inserts, updates or deletes data, including backfills and one-off data fixes.
phase: build
tier: core
---

# Safe database changes

Data mistakes are the hardest to undo. Make every change reversible,
repeatable, and visible.

## Where you are working

- The card has its own database branch. The project's usual database
  variable (for example `DATABASE_URL`, `DB_DATABASE`) already points there.
  Use it as-is. Do not edit `.env` files and do not point at another database.
- Never connect to a shared, staging or production database by hand, even if
  you find credentials somewhere.

## Migrations

- Use the project's migration tool (Alembic, Django migrations, Laravel
  migrations, Prisma, Knex, goose, golang-migrate, Flyway...). Generate the
  file with its command so naming and ordering are right.
- Never edit a migration that has already been applied or committed on the
  default branch. Add a new migration that corrects it.
- Every migration has a working down/rollback path. If it truly cannot be
  reversed (a dropped column's data), say so in the migration and the summary.
- Keep schema changes and data changes in separate migrations when the tool
  allows it.
- Prove it round-trips: migrate up, roll back one step, migrate up again.

```bash
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
php artisan migrate && php artisan migrate:rollback --step=1 && php artisan migrate
```

### Safe schema patterns

- New columns on existing tables: nullable or with a default. Make them
  NOT NULL in a later step after a backfill.
- Renames: prefer add new, copy, switch code, drop old later. A straight
  rename breaks running code.
- Indexes on large tables: use the engine's online option when available
  (`CREATE INDEX CONCURRENTLY` on PostgreSQL, outside a transaction).
- Foreign keys: check for orphan rows first or the constraint will fail.

## Seeders and fixtures

- Idempotent: running twice gives the same result. Use upsert or
  "first or create" patterns keyed on a natural unique column.

```sql
INSERT INTO roles (slug, name) VALUES ('admin', 'Admin')
ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name;
```

- No random data in seeders meant for real environments. Factories belong in
  tests.

## Destructive statements

Do not run `DROP`, `TRUNCATE`, `DELETE` without a selective `WHERE`, or
`UPDATE` without a `WHERE`, unless the card explicitly asks for it. If it does:

1. Count what will be affected with the same `WHERE` first.
2. Make sure the count matches what you expect. If it is surprising, stop and
   ask (see `ask-when-blocked`).
3. Run it inside a transaction where the engine supports it, check the
   affected row count, then commit.

## Backfills and bulk updates

- Batch by primary key range, not with `OFFSET`:

```sql
UPDATE orders SET status_v2 = status
WHERE id > :last_id AND id <= :last_id + 5000 AND status_v2 IS NULL;
```

- Make each batch restartable (the `IS NULL` guard above means a rerun skips
  done rows).
- Commit per batch, log progress, keep transactions short.
- Long jobs: start them with PRAT's `start_job` tool (see `report-progress`).

## Before and after

Record evidence for the summary:

```sql
SELECT count(*) FROM orders;                         -- before
SELECT count(*) FROM orders WHERE status_v2 IS NULL; -- remaining work
```

Run the same queries after. Row counts that change unexpectedly are a bug
until explained. The `data-analyst` helper can run these read-only checks.

## Common traps

- Model or ORM code changed but no migration generated.
- Migration works on an empty database but fails on real data (nulls,
  duplicates, orphans). Check the data first.
- Timezone-naive timestamps mixed with aware ones.
- Default values computed once at migration time when you meant per row.
- Raw SQL built with string formatting. Always use bound parameters.
- Forgetting to commit the generated migration file.
