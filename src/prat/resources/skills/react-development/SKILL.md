---
name: react-development
description: Use whenever you build or change React components, hooks, pages, forms, client state or data fetching, or write tests for React UI, including Vite, Next.js and Tailwind based frontends.
phase: build
tier: stack
---

# React development

Read `node-typescript-development` too for package manager and TypeScript
rules.

## Components

- Function components only. One component per file for anything reused.
- Props typed with a `type Props = {...}`. Avoid `React.FC`.
- Keep components small: split when a component fetches data, manages a
  form, and renders a complex layout all at once.
- Derive values during render instead of storing them in state:

```tsx
// Bad: duplicated state that drifts
const [fullName, setFullName] = useState("");
useEffect(() => setFullName(`${first} ${last}`), [first, last]);

// Good
const fullName = `${first} ${last}`;
```

- Stable, unique `key` from the data (an id), never the array index for
  lists that change.

## Hooks rules

- Call hooks at the top level, never in conditions, loops or after an early
  return.
- Custom hooks start with `use` and hold reusable stateful logic.
- Dependency arrays list everything used inside. Do not silence
  `react-hooks/exhaustive-deps`; fix the cause (move the function inside the
  effect, or wrap it in `useCallback` if it truly must be stable).

## Where state lives

| Kind | Put it in |
| --- | --- |
| UI-only (open/closed, input text) | `useState` in the nearest component |
| Shared by siblings | Lift to the common parent |
| Server data | TanStack Query (or the project's data layer), not `useState` + `useEffect` |
| App-wide client state | The project's store (Zustand, Redux, context) |
| Shareable view state (filters, tab, page) | The URL (search params) |

With TanStack Query: stable query keys including every parameter, invalidate
the right keys after mutations, and handle `isPending` and `isError`.

## Effects

Effects are for syncing with something outside React (subscriptions, DOM
APIs, timers). Not for:

- deriving data (compute in render, `useMemo` if expensive),
- reacting to a user event (do it in the event handler),
- fetching when a data library exists.

When you do use one, return a cleanup, and guard against stale async results
(AbortController or an `ignore` flag).

## Accessibility basics

- Real elements: `<button>` for actions, `<a href>` for navigation, never a
  clickable `<div>`.
- Every input has a `<label>` (or `aria-label` if no visible label).
- Images: meaningful `alt`, or `alt=""` for decoration.
- Keyboard: everything reachable with Tab, visible focus styles, Escape
  closes dialogs, focus returns to the trigger.
- Do not convey meaning with colour alone.

## Testing with Testing Library

```tsx
render(<LoginForm onSubmit={onSubmit} />);
await userEvent.type(screen.getByLabelText(/email/i), "a@b.co");
await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
expect(onSubmit).toHaveBeenCalledWith({ email: "a@b.co" });
```

- Query by role and label (what users see), not test ids or class names.
- `findBy*` for things that appear asynchronously; no arbitrary sleeps.
- Mock the network (MSW or the project's approach), not your components.

## Dev server and styling

- Vite defaults to port 5173 (next free port if taken); Next.js to 3000.
  Use the port the project or the preview environment assigns. Read the
  dev server output for the actual URL.
- Tailwind present: use utility classes and the theme tokens in the config,
  no inline `style` for things Tailwind covers, no new CSS files for one-off
  rules. Keep class order consistent with the project's formatter plugin.
- Follow the existing component library (shadcn/ui, MUI, etc.) before
  writing a new primitive.

## Common traps

- Setting state in render, causing infinite loops.
- Object or array literals as effect dependencies, re-running every render.
- Forgetting loading and error states.
- Mutating state objects instead of creating new ones.
- Hydration mismatches in SSR from `Date.now()`, `Math.random()` or
  `window` access during render.
