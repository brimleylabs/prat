---
name: node-typescript-development
description: Use whenever you write, change or test JavaScript or TypeScript on Node: packages, scripts, servers, build config, dependencies, or anything touching package.json, tsconfig.json or a lockfile.
phase: build
tier: stack
---

# Node and TypeScript development

## Package manager: the lockfile decides

| Lockfile | Use |
| --- | --- |
| `pnpm-lock.yaml` | `pnpm` |
| `yarn.lock` | `yarn` (check `packageManager` in package.json for v1 vs berry) |
| `package-lock.json` | `npm` |
| `bun.lockb` / `bun.lock` | `bun` |

- Never switch package manager and never create a second lockfile.
- Install with the frozen form when you are not changing dependencies:
  `pnpm install --frozen-lockfile`, `npm ci`, `yarn install --immutable`.
- Add a dependency only when the card needs it, with the same manager:
  `pnpm add zod`, `pnpm add -D vitest`.
- If the lockfile changed and you did not change dependencies, revert it:
  `git checkout -- pnpm-lock.yaml`.
- In a monorepo, run commands in the right workspace
  (`pnpm --filter <pkg> test`, `npm -w <pkg> run test`).

## Node version

Read `.nvmrc`, `.node-version`, `engines.node` in package.json, or the CI
config. Check `node --version` matches. If it does not and tests fail
strangely, report the mismatch instead of rewriting code around it.

## TypeScript

- Respect the project's `tsconfig.json`. Do not loosen `strict`,
  `noImplicitAny` or `strictNullChecks` to get a green build.
- No `any` in new code. Use `unknown` and narrow, or a proper type.
- No non-null assertions (`!`) to silence errors you have not reasoned about.
- `as` casts only at real boundaries (parsed JSON after validation).
  Validate external input with the project's schema library (zod, valibot...).
- Prefer `type` unions and `satisfies` for config objects:

```ts
const routes = {
  home: "/",
  settings: "/settings",
} satisfies Record<string, `/${string}`>;
```

- Type check without emitting: `tsc --noEmit` (or the project's
  `typecheck` script). Run it before finishing.

## ESM vs CJS

- `"type": "module"` in package.json means `.js` files are ESM. Use
  `import`, and include file extensions in relative imports when the
  project's `moduleResolution` is `node16`/`nodenext`.
- No `require`, `__dirname` or `module.exports` in ESM. Use
  `import.meta.url` with `fileURLToPath`, or `import.meta.dirname` on
  recent Node.
- Do not flip a package between ESM and CJS as part of an unrelated card.

## Testing

- Use what is installed: vitest or jest. Do not add the other.
- Run a single file fast: `pnpm vitest run src/foo.test.ts`,
  `npx jest src/foo.test.ts`.
- Use `vi.fn()` / `jest.fn()` for boundaries, fake timers for time.
- Reset mocks between tests (`vi.restoreAllMocks()` in `afterEach`, or the
  config option).
- Never leave `.only` or `.skip` in committed tests.

## Async and errors

- Always `await` or return promises. Floating promises lose errors.
- `Promise.all` for independent work, not sequential awaits in a loop.
- Throw `Error` objects (or subclasses), never strings.
- In servers, handle errors in one middleware or handler, not ad hoc.

## Common traps

- Lockfile churn from running a different manager or version.
- `npm install` instead of `npm ci` rewriting the lockfile.
- Editing generated files (`dist/`, generated API clients, `*.d.ts` output).
- Importing dev-only packages from runtime code.
- Reading `process.env` everywhere instead of in one validated config module.
- Default exports making renames and auto-imports unreliable; follow the
  project's convention.
- Path aliases in tsconfig that the runtime or test runner does not resolve.
