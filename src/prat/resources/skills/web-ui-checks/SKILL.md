---
name: web-ui-checks
description: Use whenever you changed anything a browser renders (pages, templates, routes, CSS, JS, assets) and need to verify it without a screenshot. Start the preview, fetch pages with curl, and check status, assets, errors and key text.
phase: verify
tier: stack
---

# Web UI checks without a screenshot

You cannot see the page, but you can prove a lot about it with HTTP. Do this
for every page your change touches, plus one page it should not have broken.

## 1. Start the preview

- Use the project's own start command (or the preview environment PRAT
  provides) and the port it assigns. Read the server output for the real URL.
- Start it in the background (PRAT's `start_job`, or the project's
  documented dev command), not as a foreground process that blocks you.
- Wait until it answers, with a bounded retry, not an endless loop:

```bash
for i in $(seq 1 30); do curl -fsS -o /dev/null "$BASE/" && break; sleep 1; done
```

## 2. Status codes

```bash
curl -sS -o /dev/null -w '%{http_code} %{url_effective}\n' "$BASE/settings"
curl -sS -o /dev/null -w '%{http_code} -> %{redirect_url}\n' "$BASE/admin"
```

- Expected pages return 200. Protected pages return the expected redirect
  (302/303 to login) or 401/403, not 500.
- Follow redirects (`-L`) only when you mean to; check the final URL.
- Check a 404 path really returns 404, if you touched routing.

## 3. Same-origin assets load

Extract the page's own CSS, JS and image URLs and check each one:

```bash
curl -sS "$BASE/settings" -o page.html
grep -oE '(src|href)="[^"]+"' page.html | cut -d'"' -f2 \
  | grep -vE '^(https?:)?//|^#|^mailto:|^data:' | sort -u \
  | while read -r p; do
      case "$p" in /*) u="$BASE$p" ;; *) u="$BASE/$p" ;; esac
      printf '%s %s\n' "$(curl -sS -o /dev/null -w '%{http_code}' "$u")" "$u"
    done
```

- Every same-origin asset should return 200 with a sensible content type
  (`curl -sI` shows `Content-Type`). A CSS URL returning `text/html` is
  usually a 404 page in disguise.
- Ignore third-party URLs; you cannot control them.
- For bundled apps (Vite, webpack), check the hashed entry files the HTML
  references, not source paths.

## 4. Server error markers

Search the HTML for signs of a broken render:

```bash
grep -inE 'Traceback|Fatal error|Parse error|Uncaught|Exception|Stack trace|Whoops|Internal Server Error|SQLSTATE|Undefined (index|variable|array key)|Warning: |Notice: |DEBUG = True|NEXT_DATA.*"err"' page.html
```

- Framework debug pages (Laravel/Symfony error pages, Django yellow debug
  page, Flask/Werkzeug debugger, Next.js error overlay markup) count as
  failures even with status 200.
- Also check the preview server's own output or log for errors logged while
  serving your requests.

## 5. Key content is present

- Grep for the text the card says should appear (a heading, a button label,
  a field name) and for text that should be gone.
- Check the `<title>` and main heading are right.
- For forms: the form exists, the `action` and `method` are right, CSRF token
  field present where the framework expects one.

```bash
grep -c 'Save changes' page.html
grep -o '<title>[^<]*</title>' page.html
```

- For client-rendered apps where the HTML is an empty shell, check the API
  endpoints the page calls instead (status, JSON shape), and say in the
  summary that rendering itself was not verified.

## 6. Report

In the summary, list each URL checked with its status, asset results
(for example "14 same-origin assets, all 200"), error scan result, and the
key text found. The `web-checker` helper can do steps 2 to 5 for you; give
it the base URL, the paths, and the text to expect.

## Common traps

- Checking the wrong port or a stale server started before your change.
- Treating 200 as success when the body is an error page or login page.
- Asset paths that work at `/` but break on nested routes (relative paths
  without a leading slash or a correct base).
- Cached assets hiding a broken build. Rebuild, and check the hashed names
  changed.
- Leaving the preview server running with no record of it. Stop what you
  started.
- Saving `page.html` inside the repo. Write scratch files outside the
  worktree or delete them before committing.
