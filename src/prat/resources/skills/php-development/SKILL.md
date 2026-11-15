---
name: php-development
description: Use whenever you write or change PHP code outside a framework-specific skill, including WordPress plugins and themes, composer packages, PHPUnit tests, or any PHP that handles user input, SQL or HTML output.
phase: build
tier: stack
---

# PHP development

Target PHP 8.x features unless `composer.json` (`require.php`) or the CI
config says otherwise. For Laravel, also read `laravel-development`.

## Basics

- `declare(strict_types=1);` at the top of new files (match the project if
  it never uses it, but do not remove it where it exists).
- Typed properties, parameter and return types, union and nullable types.
- Constructor property promotion, `readonly` for value objects, `enum` for
  fixed sets, `match` instead of long `switch` chains.
- PSR-12 formatting and PSR-4 autoloading. Run the project's formatter
  (php-cs-fixer, PHP_CodeSniffer, Pint) on files you change.
- Throw specific exceptions; never suppress errors with `@`.

## Composer

- `composer install` to install. Never `composer update` unless the card
  asks for a dependency upgrade; it rewrites `composer.lock`.
- Add one package: `composer require vendor/pkg` (dev: `--dev`). The lock
  changes only for that package and its dependencies.
- Run `composer dump-autoload` after adding classes in a new namespace path.
- Never commit `vendor/` unless the project already does.

## Testing

```bash
vendor/bin/phpunit --filter testCalculatesTotal
vendor/bin/pest --filter="calculates total"   # if the project uses Pest
```

- Data providers for input tables.
- Test behaviour through public methods; no reflection into privates.

## Security traps

**SQL injection.** Always bind parameters.

```php
$stmt = $pdo->prepare('SELECT * FROM users WHERE email = :email');
$stmt->execute(['email' => $email]);
```

Never interpolate input into SQL. Identifiers (column names for sorting)
come from an allowlist.

**XSS.** Escape on output, for the context:

```php
echo htmlspecialchars($name, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
```

**CSRF.** Every state-changing form or request carries a token that the
server verifies. GET requests never change state.

**Other:** `password_hash()` / `password_verify()` for passwords; no
`unserialize()` on user input (use JSON); validate uploaded file types by
content, store outside the web root; never pass input to `include`, `eval`,
`exec` or `shell_exec`.

## WordPress pointers

- Prefix all functions, hooks, options and globals with the plugin's or
  theme's prefix, or use namespaces.
- Nonces on every form and AJAX/REST write:
  `wp_nonce_field('acme_save', 'acme_nonce')` then
  `check_admin_referer('acme_save', 'acme_nonce')` or `wp_verify_nonce()`.
- Capabilities, not roles: `current_user_can('edit_post', $post_id)`.
- REST routes always have a real `permission_callback` (never
  `__return_true` for writes).
- Sanitize input: `sanitize_text_field()`, `sanitize_email()`, `absint()`,
  `wp_unslash()` first on superglobals.
- Escape output late: `esc_html()`, `esc_attr()`, `esc_url()`,
  `wp_kses_post()` for allowed HTML.
- Database: `$wpdb->prepare()` for every query with variables; use
  `$wpdb->prefix` for table names.
- Enqueue assets with `wp_enqueue_script` / `wp_enqueue_style`, never
  hard-coded tags.
- WP-CLI for checks and data work: `wp plugin list`, `wp option get`,
  `wp eval`, `wp db query` (read-only unless the card asks for writes).
  Use `wp search-replace --dry-run` first, always.
- Never edit WordPress core or third-party plugin files; use hooks or a
  child theme.

## Common traps

- `==` comparisons with mixed types. Use `===`.
- `empty("0")` is true.
- Array functions that reindex or keep keys unexpectedly
  (`array_filter` keeps keys; wrap in `array_values` for lists).
- Timezones: set them explicitly with `DateTimeImmutable` and
  `DateTimeZone`.
- Output before `header()` calls (BOM or whitespace after `?>`). Omit the
  closing `?>` in pure PHP files.
