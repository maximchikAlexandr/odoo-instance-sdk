## Why

`odcli run` cannot currently pass native Odoo runtime options, forcing users to change generated configuration or bypass the managed foreground path. GitHub #35 is the first product slice over the shared command architecture, so passthrough must preserve the environment binding, captured-plan parity, native TTY lifecycle, and process boundary delivered by #40/#45.

## What Changes

- Accept native Odoo arguments after `odcli run --` and preserve their values, repetition, and order without shell interpolation.
- Add the same optional native argv to `OdooInstance.run_foreground_command()` and `run_foreground()`, with the convenience method delegating to the command exactly once.
- Extend the existing shell/runtime validation boundary to reject configuration, database/credentials, addons/data paths, HTTP bind/port, and logfile overrides—including protected long-option abbreviations that Odoo's `optparse` could resolve—before command construction or process launch while allowing ordinary runtime flags.
- Append validated native arguments after generated config arguments in the one captured foreground `ProcessStep`; dry-run and execution consume that same immutable snapshot.
- Preserve the existing run ordering: an occupied port constructs no command, a free port captures the command first, dry-run previews it without use-metadata writes, and normal execution records use after capture and before execution.
- Preserve inherited stdin/stdout/stderr, process-group signals, PID identity tracking, artifact locking, cleanup, and the native Odoo exit code.
- Document delimiter passthrough and dry-run usage, add unit/integration coverage, and record the mandatory post-#35 Expression branch-versus-adapter/unwrap assessment.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `server-lifecycle`: Add validated native argv to the public foreground SDK command and define the protected runtime-identity boundary.
- `cli-odcli`: Add `odcli run -- ...` delimiter passthrough while retaining bounded dry-run and native normal execution.
- `packaging`: Complete the required post-#35 Expression payoff recheck before allowing broader adoption.

## Impact

- Public SDK: additive optional `args` parameter on `OdooInstance.run_foreground()` and `run_foreground_command()`.
- CLI: additive `odcli run -- ODOO_ARGS...` syntax; existing `run`, output-mode, context-resolution, and exit semantics remain compatible.
- Implementation: focused changes around `resources/instance.py`, the `run` Click adapter, the shared runtime-argument validator, documentation, and existing execution/CLI characterization suites.
- Dependencies: no new dependency or parallel runner/output abstraction; Expression is retained or removed only according to the recorded mandatory gate.
