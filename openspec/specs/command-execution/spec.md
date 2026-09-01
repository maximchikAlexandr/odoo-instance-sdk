# command-execution Specification

## Purpose
TBD - created by archiving change centralize-execution-dry-run-typed-output. Update Purpose after archive.
## Requirements
### Requirement: Public inspectable command contract

Every finite public SDK operation that can launch a child process SHALL expose a sibling `<operation>_command()` returning `Command[T]`. `Command[T]` SHALL expose an immutable secret-free `.plan`, stable `.commands`, and `.run() -> T`; the existing finite convenience operation SHALL delegate to that command object and SHALL NOT reconstruct planning or execution inputs. An explicitly documented unbounded streaming coordinator MAY instead build and run a fresh finite command per iteration when no finite snapshot can describe all future launches; it SHALL NOT launch directly or reuse one stale command across iterations.

#### Scenario: Convenience method delegates once

- **WHEN** a process-spawning convenience method is called
- **THEN** it constructs its sibling command exactly once and returns that command's `.run()` result
- **AND** no separate argv, cwd, environment, stdin, or action plan is constructed

#### Scenario: Unbounded monitor watch advances one tick

- **WHEN** `EnvironmentMonitor.watch()` produces its next snapshot
- **THEN** it constructs a fresh immutable `snapshot_command()` exactly once for that tick and yields that command's `.run()` result
- **AND** every process-backed probe runs through `internal/proc`
- **AND** no command or consumption ledger is reused across ticks

#### Scenario: Command remains stable after construction

- **WHEN** client configuration, ambient environment variables, or mutable option inputs change after a command is built
- **THEN** `.plan` and `.commands` remain unchanged
- **AND** `.run()` consumes the values captured before those changes

### Requirement: Immutable serializable execution plan

`ExecutionPlan` SHALL contain ordered frozen `ProcessStep` and `ActionStep` projections, read-only planning observations, warnings, and a stable fingerprint. A `ProcessStep` SHALL preserve argument boundaries and expose executable, redacted argv, `shlex.join` display text, resolved cwd, sanitized environment policy and overrides, input preview, timeout, execution mode, and read-only/mutating/interactive/long-running classification. An `ActionStep` SHALL describe the actual in-process effect and SHALL NOT synthesize shell or Python source.

#### Scenario: Process projection preserves boundaries

- **WHEN** a captured argv contains spaces, quotes, or shell metacharacters inside one argument
- **THEN** the plan preserves that value as one argv array element
- **AND** the shell-readable string is derived only for display and is never executed

#### Scenario: In-process action is represented honestly

- **WHEN** an operation plans an HTTP, database, filesystem, lock, signal, or cleanup action with no child command
- **THEN** the plan contains an `ActionStep` describing that action
- **AND** it contains no invented shell command or pseudo-Python

### Requirement: One private executable snapshot

Command construction SHALL capture one immutable private executable snapshot. `Command.run()` SHALL be repeatable: each invocation SHALL create an independent per-run consumption ledger over that same snapshot and SHALL share no consumption state with earlier, later, or concurrent invocations. Within one run, execution SHALL request each captured process step through its identifier exactly once. An unplanned, substituted, or duplicate request SHALL fail before the requested child starts. An omitted step SHALL fail the run when the operation callback completes and the ledger is checked; effects completed earlier in that run are not promised to be absent or rolled back unless the operation's existing lifecycle contract provides that guarantee.

#### Scenario: Preview and execution have parity

- **WHEN** a recording executor runs a previously inspected command
- **THEN** applying the production redaction function to each recorded executor input yields the corresponding public process step exactly

#### Scenario: Operation attempts an unplanned launch

- **WHEN** operation lifecycle code requests a child command not present in the captured snapshot
- **THEN** execution fails before that child starts

#### Scenario: The same command runs twice

- **WHEN** a caller invokes `.run()` twice on one immutable command
- **THEN** each invocation starts with a fresh empty consumption ledger and executes the same captured snapshot
- **AND** consumption recorded by either invocation does not affect the other

#### Scenario: Operation callback omits a planned step

- **WHEN** an operation callback returns after consuming only a prefix or subset of its captured process steps
- **THEN** the completion ledger check fails that run and identifies the omitted step
- **AND** the contract does not claim that process or action effects completed earlier in that run never started

### Requirement: Secret-free projection and fingerprint

Public plans, `.commands`, `repr`, exceptions, Rich/JSON/TOON output, observations, warnings, and fingerprints SHALL contain no password, token, private environment value, generated secret-file content, or other configured secret. The fingerprint SHALL be computed from deterministic canonical serialization of the redacted public projection and SHALL exclude its own field.

#### Scenario: Secret appears in executable inputs

- **WHEN** exact private argv, environment, stdin, or generated configuration contains a secret
- **THEN** execution retains the exact private value
- **AND** every public surface contains only the configured redaction marker
- **AND** changing only that secret does not expose it through the fingerprint

#### Scenario: Odoo shell input is previewed

- **WHEN** exact Python source is captured for Odoo shell stdin, `-c`, or a generated script file
- **THEN** the plan shows the full redacted source and real Odoo argv/config/database/shell arguments
- **AND** ordinary in-process Python functions are not rendered as source

### Requirement: Planning is non-mutating and observable

Building a command SHALL start none of its planned processes and SHALL perform no filesystem, database, network, catalog, lock, or process mutation. Bounded read-only probes required for resolution MAY execute through the shared process boundary and SHALL be recorded as observations with `read_only=true` and `executed_during_planning=true`.

#### Scenario: Dry command construction

- **WHEN** a command is built with a fake executor and mutation sentinels
- **THEN** no planned process, file creation, catalog migration, database mutation, network mutation, lock acquisition, or prompt occurs

#### Scenario: Read-only Git probe is required

- **WHEN** planning executes Git status, rev-parse, merge-base, or an equivalent bounded probe
- **THEN** the redacted probe and relevant result metadata appear in plan observations
- **AND** the probe runs through `internal/proc`

### Requirement: Fail-closed stale-plan revalidation

Before the first mutating step, `.run()` SHALL revalidate captured volatile preconditions including applicable paths, Git identity/HEAD, database/environment identity, ports, locks, and deterministic future path collisions. A changed precondition SHALL raise `StalePlanError`; execution SHALL NOT replan, substitute a new value, or start a mutating step.

#### Scenario: Git HEAD changes after preview

- **WHEN** a command captured one Git revision and the relevant revision changes before `.run()`
- **THEN** `.run()` raises `StalePlanError` before mutation
- **AND** it does not rebuild commands against the new revision

#### Scenario: Future path collides

- **WHEN** a deterministic temp/config path selected during planning exists at execution time
- **THEN** exclusive creation fails closed as stale
- **AND** execution does not overwrite or silently choose another path

### Requirement: All later process steps are known before mutation

An operation SHALL capture every child-process argv that can run after its first mutation before that mutation begins. If a later command genuinely depends on a prior mutating result, the operation SHALL expose a real domain phase boundary rather than use a hidden dynamic step or generic continuation engine.

#### Scenario: Later argv cannot be resolved safely

- **WHEN** planning cannot determine a later process argv without executing an earlier mutation
- **THEN** command construction returns a typed planning error or an explicit phase result
- **AND** it does not append the process invisibly during `.run()`

### Requirement: Single production process boundary

Every production `subprocess.run` and `subprocess.Popen` launch SHALL occur only inside `odoo_instance_sdk.internal.proc`. The boundary SHALL support captured output, inherited stdio/TTY, foreground and long-running handles, timeouts, sanitized environments, stdin bytes/scripts, waiting, recording/fake execution, and process termination. Execution SHALL always use argv with `shell=False`.

#### Scenario: Production module launches directly

- **WHEN** an AST contract scan finds a direct subprocess launch outside `internal/proc`
- **THEN** the architecture gate fails with the file and line

#### Scenario: Foreground process is interrupted

- **WHEN** an inherited-stdio foreground command receives Ctrl+C or its wait path raises
- **THEN** the boundary performs the existing bounded TERM/KILL/reap behavior for the owned process group
- **AND** returns the established interrupt semantics without orphaning descendants

#### Scenario: Windows termination needs taskkill

- **WHEN** Windows process-tree termination invokes `taskkill`
- **THEN** `taskkill` is a captured process step executed through `internal/proc`

### Requirement: Complete existing launch-site migration

The migration SHALL cover all current production launch sites, including Odoo captured/start/foreground/shell processes, Git and changed-file probes, worktrees, uv environments/compile/install, Docker Compose, PostgreSQL/psql/pg_restore, backup validation, pgAdmin helpers, dependency probes, ACL and disk usage, and platform termination. Legacy runner abstractions and module-local subprocess reconstruction SHALL be removed after their callers migrate.

#### Scenario: Migration completes

- **WHEN** the architecture gate and process-family parity suites run on the completed change
- **THEN** the only production subprocess launch nodes are in `internal/proc`
- **AND** no old executor or duplicate preview planner remains reachable

### Requirement: Typed planning pipeline boundary

Sequential pure resolve, validation, selection, normalization, and capture stages SHALL use explicit typed expected-error results. Expression MAY implement those internal stages, but Expression values or types SHALL NOT appear in public SDK contracts, Click registration, serializers, process effects, locks, cleanup, rollback, compensation, or foreground lifecycle.

#### Scenario: Expected input failure

- **WHEN** pure planning rejects an expected invalid input
- **THEN** the internal pipeline returns a concrete `PlanError`
- **AND** the public SDK/CLI boundary exposes the project's concrete typed exception or error document without Expression types

#### Scenario: Effect adapter fails

- **WHEN** the OS raises timeout, spawn, wait, or cleanup failure during execution
- **THEN** the concrete effect/domain exception propagates according to existing lifecycle rules
- **AND** it is not wrapped in a universal planning `Result`

