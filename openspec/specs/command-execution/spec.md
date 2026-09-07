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

### Requirement: Deadline-bound attempts are plan-visible

When an operation runs multiple captured process attempts under one monotonic
budget, its `ExecutionPlan.observations` SHALL contain a frozen,
serializable private deadline observation with the attempt step IDs and total
budget. The observation SHALL describe the stable budget only; a
per-invocation monotonic start time SHALL remain private run state. The shared
process boundary SHALL compute each attempt's remaining timeout from that
single deadline and SHALL receive the original captured process step together
with explicit deadline context, never a substituted `PreparedStep`. This
observation is plan-visible but is not part of the public SDK model vocabulary.

#### Scenario: Deadline policy remains visible while runtime controls vary

- **WHEN** a recording executor runs two inspected attempts under one shared deadline
- **THEN** the public plan identifies both step IDs and the total budget
- **AND** the ledger and recording executor receive the exact captured steps from that plan
- **AND** the subprocess timeout and server statement timeout use no more than the current monotonic remainder
- **AND** an exhausted or sub-millisecond remainder starts no process

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

Building a command SHALL start none of its planned processes and SHALL perform no filesystem, database, network, catalog, lock, or process mutation. Bounded read-only probes required for resolution MAY execute through the shared process boundary and SHALL be recorded as observations with `read_only=true` and `executed_during_planning=true`. Resolving an initialized project, monitoring it, or producing any dry-run SHALL NOT register/upsert the project or runtime. Successful non-preview init and normal foreground execution/lifecycle MAY perform the registration writes explicitly authorized by their own execution requirements, after planning and outside resolution.

#### Scenario: Dry command construction

- **WHEN** a command is built with a fake executor and mutation sentinels
- **THEN** no planned process, file creation, catalog migration, database mutation, network mutation, lock acquisition, or prompt occurs

#### Scenario: Read-only Git probe is required

- **WHEN** planning executes Git status, rev-parse, merge-base, or an equivalent bounded probe
- **THEN** the redacted probe and relevant result metadata appear in plan observations
- **AND** the probe runs through `internal/proc`

#### Scenario: Project preview does not mutate catalogue
- **WHEN** a command is built or dry-run from an unregistered initialized project with catalogue mutation sentinels
- **THEN** read-only observations may be returned but no project, environment, runtime, lifecycle, or migration write occurs

#### Scenario: Registration occurs only during authorized execution
- **WHEN** a successfully planned normal init or foreground lifecycle operation crosses its explicit execution boundary
- **THEN** its specified project registration write may occur without making ordinary resolution or command construction mutating

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

### Requirement: Plan preconditions survive preview

An inspectable command plan SHALL represent checked preconditions as typed JSON-safe observations with stable identity, status, and sanitized detail. A failed precondition SHALL prevent normal execution, but preview SHALL remain executable as a read-only inspection operation and SHALL return the complete plan. Preconditions SHALL not introduce a second executor or rerun process construction.

#### Scenario: Failed precondition blocks only mutation
- **WHEN** a captured command has a failed port or active-connection precondition
- **THEN** normal execution stops before mutation while preview returns the same captured plan with the failure recorded

### Requirement: Shared Rich plan summary

Execution plans SHALL expose enough typed goal, target, mutation, precondition, and warning information for the CLI output boundary to build one concise Rich summary. Exact private execution details and the existing redacted public snapshot SHALL remain the source of truth for execution and machine formats.

#### Scenario: Projection does not alter snapshot
- **WHEN** Rich and JSON previews render the same captured command
- **THEN** renderer selection does not mutate, rebuild, or omit fields from the underlying public plan

### Requirement: Real-time observed captured output

When a caller opts into output observation for a captured process step, the single production process boundary SHALL emit sanitized stdout and stderr events while the child is still running, tagged with the captured `step_id`, while preserving the final captured `ProcessResult` and exit code. Before observer delivery, a stateful per-stream incremental decoder/redactor SHALL preserve all canonical structural detectors from `internal/proc/redaction.py`: password/token-style assignments, Bearer and Basic credentials, Authorization/Proxy-Authorization/Cookie/Set-Cookie headers, URI userinfo, and JWTs, plus configured/captured runtime secrets. It SHALL carry algorithmically sufficient detector state and withhold every unresolved candidate from its earliest possible prefix through its value until it can emit safe text or a redaction marker across arbitrary byte, decoder, and observer chunk boundaries. No ordered combination of observer events SHALL reconstruct any raw detected secret. Success, failure, timeout, and Ctrl-C SHALL resolve/redact incomplete candidates before safe flush. The final captured result SHALL retain the current canonical whole-value projection semantics. This SHALL NOT require a second process executor or caller-owned pipe reader.

#### Scenario: Output arrives before process completion

- **WHEN** a controlled process writes and flushes one line and then remains running
- **THEN** the observer receives the sanitized stream event before it receives the completed event
- **AND** the final result still contains the captured output and original exit code

#### Scenario: Canonical structural detector crosses chunks

- **WHEN** any canonical assignment, credential scheme, sensitive header, URI-userinfo, or JWT detector has a significant split within its prefix or value across byte, decoder, or observer chunks
- **THEN** the redactor withholds the unresolved candidate and no observer event sequence exposes fragments that reconstruct its raw value
- **AND** safe surrounding text and the redaction marker are eventually emitted in order

#### Scenario: Captured runtime secret crosses chunks

- **WHEN** a configured or captured runtime secret is split at any significant byte or decoder boundary
- **THEN** no observer event sequence exposes fragments that reconstruct the secret and safe surrounding text is emitted in order

#### Scenario: Incomplete candidate reaches terminal flush

- **WHEN** success, failure, timeout, or Ctrl-C occurs while a structural candidate or multibyte character is incomplete
- **THEN** final flush redacts or safely resolves the retained candidate without emitting reconstructable raw fragments

#### Scenario: Final capture keeps canonical semantics

- **WHEN** observed output contains any value recognized by the current canonical whole-value projection
- **THEN** the final captured projection redacts it with the unchanged canonical semantics independently of observer chunking

#### Scenario: Machine command does not opt in

- **WHEN** a bounded JSON or TOON command executes without output observation
- **THEN** no stream event is written to stdout or stderr by the output renderer
- **AND** stdout remains one final document

### Requirement: Bounded timeout diagnostics

A timed-out captured process SHALL terminate and reap through `internal/proc`, and its typed timeout failure SHALL retain elapsed time plus bounded sanitized tails for available stdout and stderr with independent truncation indicators. The retained tail SHALL preserve the newest diagnostic output rather than allowing an arbitrarily long startup prefix to hide the final cause.

#### Scenario: Timeout after long startup output

- **WHEN** a process exceeds its timeout after writing more output than the configured diagnostic bound
- **THEN** the failure reports elapsed time, the bounded newest stdout/stderr tails, and truncation status
- **AND** the process and owned descendants are not left running

#### Scenario: Timeout tail contains a secret

- **WHEN** timed-out output contains a captured secret
- **THEN** the exception, observer failure event, Rich diagnostic, and machine error details contain only the redacted value

### Requirement: Captured stdin is delivered exactly without deadlock or disclosure

The single production process pump SHALL write the immutable `PreparedStep.stdin` byte snapshot to child stdin without decoding, normalization, logging, observer delivery, or inclusion in captured output. It SHALL write stdin concurrently with draining stdout and stderr, close stdin after all bytes are written, and close stdin during early exit, timeout, interruption, or write failure without leaving writer, reader, child, or owned descendants running.

#### Scenario: Large stdin and output proceed concurrently

- **WHEN** a captured child emits enough stdout or stderr to fill a pipe before consuming a large `PreparedStep.stdin` snapshot
- **THEN** the pump concurrently drains output and delivers the exact stdin bytes, closes stdin, and completes without deadlock
- **AND** no stdin byte is emitted to the observer or projected output

#### Scenario: Child exits before consuming stdin

- **WHEN** a child exits while the pump is writing the captured stdin snapshot
- **THEN** the pump closes stdin and both output pipes, reaps the child, and preserves the established process-result semantics without hanging

#### Scenario: Timeout while stdin remains

- **WHEN** the captured timeout expires before a child consumes all stdin
- **THEN** the pump stops and closes the stdin writer, terminates and reaps the exact owned process group, finishes pipe cleanup, and returns the bounded sanitized timeout diagnostics

### Requirement: One internal process pump serves every captured pipe path

Ordinary observed/timeout capture and the existing limited-output API SHALL delegate to one common pump inside `internal/proc`. `run_captured_limited()` SHALL preserve immediate child termination when either output stream would exceed its configured byte limit. `internal/proc` SHALL NOT contain a second pipe-drain, timeout, or process-cleanup algorithm.

#### Scenario: Limited output crosses its bound

- **WHEN** `run_captured_limited()` receives a chunk that would exceed the configured stream limit
- **THEN** the common pump immediately terminates and reaps the child and returns the existing limit failure semantics

#### Scenario: Architecture inventory is checked

- **WHEN** the production process architecture is inspected by its regression gate
- **THEN** ordinary capture and limited capture are shown to use the same pipe, timeout, stdin, and cleanup implementation

### Requirement: Logical progress corresponds to completed effects

The existing command run context SHALL emit start, progress, completion, and failure events for planned process and action steps. An action completion event SHALL be emitted only after that action's effect and required postcondition finish; consuming an action through `RunContext.action()` alone SHALL NOT mark it completed. Progress events MAY report completed units and a total only when the total is reliable, and SHALL NOT imply effect completion.

#### Scenario: Action fails after it starts

- **WHEN** a planned action starts and its effect raises before its postcondition
- **THEN** observers receive started then failed for that action and never completed

#### Scenario: Reliable byte total

- **WHEN** an action reports received bytes and a trustworthy total byte count
- **THEN** the observer may derive a percentage from those byte units

#### Scenario: Unknown duration

- **WHEN** a long action has no trustworthy total
- **THEN** its events expose status and elapsed time without a percentage or synthetic time estimate

### Requirement: Interrupted bounded execution closes observation

When a bounded command is interrupted, the process boundary and run context SHALL close all owned processes, response/file handles, and started progress steps before propagating the interrupt. Observer and renderer cleanup SHALL NOT replace exit code 130 or the operation's typed retained-resource context.

#### Scenario: Ctrl-C during a captured step

- **WHEN** Ctrl-C interrupts a bounded command while a captured process or action is running
- **THEN** the active step is closed as failed/interrupted, owned resources are cleaned up, and the CLI exits 130
- **AND** no live renderer control sequence contaminates a final machine document
