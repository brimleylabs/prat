# Databases: Doltgres branches, the pinned build, and the PostgreSQL fallback

PRAT gives every card its own copy of a project's data, so an agent can migrate, seed and break
things without touching anyone else's work. How it does that depends on the database.

| | Doltgres (and Dolt) | Plain PostgreSQL (the fallback) |
| --- | --- | --- |
| What a card gets | its own branch, `card-<id>` | its own copy, `<database>__card_<id>` |
| Review shows | every table the card changed, schema and rows | nothing about data |
| Approve | merges the data, then the code (both or neither) | merges the code; schema reaches the project database through its migrations |
| Undo and rewind | yes, data and code together | code only |
| Cost to create | instant | a template copy (fast) or `pg_dump` when the database is in use |

## How a project gets one

When the setup agent proposes a project file and this machine has a database server for
projects (`project_database_url`), PRAT creates the project's database there, records its
address as `PRAT_DATABASE_URL` in the machine-only `$PRAT_HOME/environments/<slug>.toml`
(never in the repository), and the agent declares it:

```toml
[[workspace.database]]
name = "app"
kind = "doltgres"
url_env = "PRAT_DATABASE_URL"   # where PRAT finds the address (machine-only)
app_url_env = "DATABASE_URL"    # the variable the application reads
```

For each card, PRAT points `DATABASE_URL` at the card's branch (or copy) for the agent, the
gates and the card's preview. Environments run from your checkout get the default branch.

## The automatic fallback

Some applications use SQL that Doltgres does not support yet. When a setup card's checks fail on
Doltgres, and this machine has `fallback_database_url` (a plain PostgreSQL server), PRAT runs the
same checks again on a fresh PostgreSQL database. If every check passes there, the project
moves to PostgreSQL for good: its `PRAT_DATABASE_URL` points at the new database, the reason is
recorded as `PRAT_DATABASE_FALLBACK`, and the card's review shows a `database-fallback` result
next to the gates. If the checks fail on PostgreSQL too, the problem is not the database and
nothing changes.

A project file can also ask for PostgreSQL directly with `kind = "postgres"`, and a `doltgres`
database whose address turns out to be plain PostgreSQL is treated as one.

Keep the fallback server separate from any PostgreSQL your own applications use: PRAT creates
and drops databases on it.

## The pinned Doltgres build

Doltgres is young, and real applications find gaps. These four are fixed in pull requests that
are not in a release yet, so PRAT installations run a build with them applied:

| Fix | Pull request | Without it |
| --- | --- | --- |
| Aggregate `FILTER (WHERE ...)` | [dolthub/doltgresql#3836](https://github.com/dolthub/doltgresql/pull/3836) | `function filters are not yet supported` |
| `string_agg` results longer than 1024 characters | [dolthub/doltgresql#3837](https://github.com/dolthub/doltgresql/pull/3837) | silently cut at 1024 |
| Partial index predicate that evaluates to NULL | [dolthub/dolt#12465](https://github.com/dolthub/dolt/pull/12465) | the insert panics |
| NULL histogram bound in join estimates | [dolthub/go-mysql-server#4150](https://github.com/dolthub/go-mysql-server/pull/4150) | the query panics |

Build it with [`scripts/build-doltgres.sh`](../scripts/build-doltgres.sh): it clones the three
repositories at the upstream commits the fixes were tested on, applies each fix branch from the
`brimleylabs` forks, and builds `doltgres`. It needs Go and, on macOS, `brew install icu4c`.

```bash
scripts/build-doltgres.sh ~/.prat/bin/doltgres
```

When DoltHub merges a fix and releases it, remove its line from `FIXES` in the script. When the
list is empty, use a stock Doltgres release and retire the script.
