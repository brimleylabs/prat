---
name: go-development
description: Use whenever you write, change or test Go code: packages, HTTP services, CLIs, goroutines and channels, go.mod dependencies, or table-driven tests run with go test.
phase: build
tier: stack
---

# Go development

## Standard library first

- `net/http` (Go 1.22+ has method and path patterns:
  `mux.HandleFunc("GET /items/{id}", h)` with `r.PathValue("id")`),
  `encoding/json`, `log/slog`, `database/sql`, `context`, `errors`,
  `testing`, `slices`, `maps`.
- Add a third-party module only if the card needs it and the stdlib is
  clearly insufficient. Use what the project already depends on first.

## Errors

- Return errors, do not panic, except for truly impossible states at init.
- Wrap with context using `%w`:

```go
if err := store.Save(ctx, inv); err != nil {
    return fmt.Errorf("save invoice %d: %w", inv.ID, err)
}
```

- Check with `errors.Is` / `errors.As`, never by comparing strings.
- Sentinel errors as `var ErrNotFound = errors.New("not found")`.
- Handle every error once: either log it or return it, not both.
- Error strings are lowercase with no trailing punctuation.

## Context

- `ctx context.Context` is the first parameter of anything that does I/O or
  may block.
- Pass it down; never store it in a struct. Never pass `nil`; use
  `context.TODO()` if truly unknown.
- Respect cancellation in loops: `select { case <-ctx.Done(): return ctx.Err() ... }`.
- Use `r.Context()` in HTTP handlers, `QueryContext` / `ExecContext` for SQL.

## Concurrency and goroutine leaks

- Every goroutine has a clear way to exit (context cancel, closed channel,
  finished work). If you cannot say how it stops, it leaks.
- Use `errgroup.Group` (with context) or `sync.WaitGroup` to wait.
- The sender closes a channel, never the receiver.
- Unbuffered sends block forever if nobody reads; make sure a reader exists
  or use a buffered channel sized for the producers.
- Protect shared state with `sync.Mutex` or confine it to one goroutine.
- `time.After` in a loop allocates timers; use `time.NewTimer` / `Ticker`
  and stop them.
- Run tests with `-race` when touching concurrent code.

## Tests: table-driven

```go
func TestParseSize(t *testing.T) {
    tests := []struct {
        name    string
        in      string
        want    int64
        wantErr bool
    }{
        {"bytes", "10", 10, false},
        {"kilobytes", "2k", 2048, false},
        {"garbage", "x", 0, true},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := ParseSize(tt.in)
            if (err != nil) != tt.wantErr {
                t.Fatalf("err = %v, wantErr %v", err, tt.wantErr)
            }
            if got != tt.want {
                t.Errorf("got %d, want %d", got, tt.want)
            }
        })
    }
}
```

- `t.Helper()` in helpers, `t.TempDir()` for files, `t.Setenv()` for env,
  `httptest.NewServer` / `httptest.NewRecorder` for HTTP.
- Run narrow then wide: `go test ./pkg/size -run TestParseSize -v`, then
  `go test ./...`.

## Tooling and hygiene

```bash
gofmt -l .          # must print nothing
go vet ./...
go test -race ./...
go mod tidy         # only if you changed imports; check the go.sum diff
```

- Use the project's linter config (`golangci-lint run`) if present.
- Respect the `go` and `toolchain` lines in go.mod; do not bump them.
- Do not commit `vendor/` changes unless the project vendors.

## Design

- Accept interfaces, return concrete types. Define small interfaces where
  they are used, not next to the implementation.
- Zero values should be useful; constructors only when needed.
- Keep `main` thin: parse config, wire dependencies, call `run(ctx)` that
  returns an error.
- Package names short, lowercase, no `util` / `common` dumping grounds.

## Common traps

- Loop variable capture is fixed since Go 1.22, but not if go.mod says
  an older `go` version.
- Nil map writes panic; initialise with `make`.
- `defer` inside a loop holds resources until the function returns.
- Forgetting `resp.Body.Close()` and `rows.Close()`, and not checking
  `rows.Err()`.
- Ignoring the error from `json.NewDecoder(...).Decode`.
