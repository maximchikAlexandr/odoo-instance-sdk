## Why

Process-spawning SDK operations currently construct and execute commands through multiple unrelated seams, while CLI previews and bounded output are assembled separately from execution. This makes dry-run parity, redaction, typing, and future command families unsafe to extend; GitHub #45 and #40 therefore require one inspectable execution contract before #35, #33, and later subprocess-heavy work proceeds.

## What Changes

- Introduce an immutable public `Command[T]` and secret-free `ExecutionPlan` containing ordered `ProcessStep` and `ActionStep` projections, planning observations, warnings, and a stable fingerprint.
- Capture one private executable snapshot per operation; preview renders that snapshot after redaction and `.run()` consumes it without reconstructing argv, cwd, environment, stdin, or scripts.
- Route every production child-process launch through `internal/proc`, covering captured, inherited-stdio, foreground/long-running, timeout, stdin, process-handle, termination, recording, and fake-executor paths.
- Add sibling `*_command()` methods to finite public process-spawning SDK operations and make existing convenience methods delegate to them while preserving existing domain plans such as `EnvironmentCheckoutPlan`; keep `EnvironmentMonitor.watch()` as an explicit unbounded coordinator that builds a fresh `snapshot_command()` per tick.
- Add a shared command-local `--dry-run` option to eligible mutating or spawning CLI leaves; dry-run builds the same command once, emits its plan, never prompts, and never executes planned effects.
- Extend the existing `commands/output.py` boundary into one typed document/emission path for bounded Rich/JSON/TOON results, while keeping native passthrough and JSONL streaming as explicit tested exceptions.
- Use Expression only for pure sequential planning, validation, normalization, and expected-error stages; keep the library out of public SDK types, Click registration, cleanup, locking, rollback, and process lifecycle.
- Add static and contract gates for direct subprocess launches, output-boundary bypasses, explicit production `Any`/bare `object` annotations, plan/execution parity, redaction, and the lightweight startup budget established by GitHub #32.
- Migrate existing production launch sites and tests to the common seams; remove superseded runners and duplicate planners rather than retaining parallel compatibility implementations.

## Capabilities

### New Capabilities

- `command-execution`: Defines inspectable commands, immutable executable snapshots, process/action steps, planning observations, stale-plan behavior, centralized process execution, redaction, and the migration invariant for every production launch site.

### Modified Capabilities

- `models-types`: Adds the public generic command and frozen execution-plan model vocabulary plus concrete recursive JSON values and typed plan/stale-plan errors.
- `cli-odcli`: Adds universal command-local dry-run for eligible leaves, thin callback rules, and a single typed bounded-output boundary with explicit native-stream exceptions.
- `development-environment`: Preserves checkout domain planning while adding executable `checkout_command()`/related command primitives and exact preview/run parity.
- `environment-monitor`: Adds inspectable finite snapshot commands while preserving `watch()` as an unbounded fresh-snapshot streaming coordinator.
- `server-cli`: Routes captured Odoo commands through the shared process-step executor.
- `server-lifecycle`: Adds platform-independent inspectable command primitives for start, foreground run, shell, shell-script, and stop execution without weakening lifecycle cleanup or native TTY behavior.
- `local-odoo-testing`: Makes test planning produce the same captured command used by execution and exposes it through the shared SDK/CLI dry-run contract.
- `postgres-cluster`: Routes Docker Compose and PostgreSQL child processes through the shared command/executor contract while retaining ownership and secret guarantees.
- `project-database-preparation`: Makes restore and administrator-reset subprocess phases inspectable without replacing its explicit lock, retention, and compensation lifecycle.
- `packaging`: Adds the bounded Expression dependency and repository gates that enforce process, output, production typing, and lightweight startup architecture rules.

## Impact

The public SDK gains additive command/plan types and sibling methods while existing convenience methods and documented CLI behavior remain compatible. The implementation touches package exports, resources, process-heavy internal modules, `commands/output.py`, Click leaves, tests, README/SDK documentation, `AGENTS.md`, dependency metadata, and CI contracts. No workflow engine, plugin/provider framework, Click replacement, replayable plan format, or generic renderer hierarchy is introduced.
