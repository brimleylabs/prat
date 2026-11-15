---
name: laravel-development
description: Use whenever you work in a Laravel app: routes, controllers, Eloquent models, migrations, seeders, factories, form requests, queues, Blade, Livewire or Inertia views, config, or Pest/PHPUnit tests run with php artisan test.
phase: build
tier: stack
---

# Laravel development

Read `php-development` too for general PHP rules.

## Artisan first

Generate files with artisan so names, namespaces and locations are right:

```bash
php artisan make:model Invoice -mfs     # model + migration + factory + seeder
php artisan make:controller InvoiceController --resource
php artisan make:request StoreInvoiceRequest
php artisan make:job SendInvoiceReminder
php artisan make:test InvoiceTest       # add --pest if the project uses Pest
php artisan route:list --path=invoices
```

Check the Laravel version in `composer.json` and follow its structure (for
example, Laravel 11+ registers middleware and routing in `bootstrap/app.php`).

## Migrations, seeders, factories

- Every migration has a working `down()`. Never edit a migration that has
  already run on the default branch; add a new one.
- Round-trip: `php artisan migrate`, `migrate:rollback --step=1`, `migrate`.
- Never run `migrate:fresh` or `db:wipe` unless the card asks. They wipe
  the database.
- Seeders are idempotent: `updateOrCreate` / `firstOrCreate` keyed on a
  unique column.
- Factories for tests; keep realistic states (`->unpaid()`, `->overdue()`).
- See `safe-database-changes`.

## Eloquent

- N+1: eager load what you iterate. `Invoice::with('customer')->get()`.
  Turn on `Model::preventLazyLoading(! app()->isProduction())` if the
  project allows it, to catch it in tests.
- Mass assignment: define `$fillable` (or the project's `$guarded`
  convention). Never `$guarded = []` with `$request->all()`.
- Use `$request->validated()` when creating or updating from input.
- Casts for dates, enums, booleans and JSON (`casts()` method or `$casts`).
- `chunkById()` or `lazyById()` for large sets, not `all()`.
- Transactions for multi-row writes: `DB::transaction(fn () => ...)`.

## Validation and authorization

- Form requests hold validation rules and `authorize()`. Controllers stay
  thin.
- Policies for model authorization; `$this->authorize(...)` or
  `Gate::authorize(...)`.
- Never trust IDs from input without scoping them to the current user or
  tenant.

## Config and env

- Read settings through `config('services.stripe.key')`, never `env()`
  outside `config/*.php`. `env()` returns null once config is cached.
- Add new settings to a config file with an `env()` default. Do not edit
  `.env` files; mention any new variable in your summary and in
  `.env.example` if the project keeps one.

## Queues and scheduling

- Jobs must be idempotent and serializable (pass IDs or models, not
  closures or open resources).
- Set `$tries` / `$backoff` and handle `failed()`.
- In tests: `Queue::fake()` then `Queue::assertPushed(...)`.

## Views

- **Blade:** `{{ }}` escapes; `{!! !!}` only for trusted HTML. `@csrf` in
  every form.
- **Livewire:** public properties are user-editable, so validate and lock
  (`#[Locked]`) sensitive ones.
- **Inertia:** pass only the props the page needs; never serialize whole
  models with hidden fields.

## Testing

```bash
php artisan test --filter=InvoiceTest
php artisan test --parallel           # if the project is set up for it
```

- `RefreshDatabase` (or the project's trait) in feature tests.
- Use `Http::fake()`, `Mail::fake()`, `Storage::fake()`, `Event::fake()`.
- Assert on responses: `->assertOk()`, `->assertJsonPath(...)`,
  `->assertSessionHasErrors(...)`.

## Running locally

- `php artisan storage:link` once if public files are served from storage.
- Without `artisan serve`, run from `public/`:
  `php -S 127.0.0.1:8000 -t public public/index.php`.
- After changing config or routes in a cached environment:
  `php artisan optimize:clear`.

## Common traps

- `env()` in app code, null in cached config.
- Forgetting `@csrf`, getting 419 responses.
- Lazy-loading in loops and API resources.
- `composer update` instead of `composer install` (lockfile churn).
- Committing `storage/` logs, `bootstrap/cache/*.php`, or `public/storage`.
