# Spec Delta

## Purpose

Give agents one bounded, non-interactive debug call against a ready local Odoo environment, while one private session keeps the process, breakpoints, and reproduction command alive between calls.

## ADDED Requirements

### Requirement: One intention returns one bounded result

`odcli debug` and the public debug resource SHALL expose `start`, `continue`, `context`, `eval`, `step`, `pause`, `restart`, and `stop`. Each invocation SHALL perform one intention, SHALL NOT require a TTY or prompt, and SHALL return one finished result. `start`, `continue`, `context`, `step`, `pause`, `restart`, and `stop` SHALL return one immutable snapshot. `eval` SHALL return one immutable value. The CLI SHALL render that result through the shared Rich, JSON, and TOON envelope. `--json` SHALL remain a Click usage error with exit code `2`.

The public resource and the CLI SHALL share the same inputs, results, and failures. The public surface SHALL NOT expose a socket path, DAP port, thread id, frame id, request id, or variable reference.

#### Scenario: Separate processes continue one session

- **WHEN** separate non-interactive processes run `start`, then `continue` or a bounded wait, then `eval` or `step`, then `stop` for one ready environment
- **THEN** each process exits with one bounded result and no process waits at a prompt

#### Scenario: Machine formats match

- **WHEN** the same successful snapshot or value, or the same failure, is rendered as JSON and TOON
- **THEN** strict decoding yields the same envelope, and the debug payload uses schema version 1

### Requirement: Debug requires one ready environment

Debug SHALL resolve the environment with the existing instance resolver. The ready environment UUID SHALL be the only session key. A project-only context SHALL fail before a daemon, socket, or process is created. The global `--env` override MAY select an environment outside the worktree. Debug SHALL NOT add its own environment selector or a public session selector.

#### Scenario: Worktree selects the session

- **WHEN** `odcli debug context` runs inside a registered ready worktree
- **THEN** the snapshot `environment_id` is that environment UUID

#### Scenario: Project context is rejected

- **WHEN** debug is invoked where only a project resolves
- **THEN** the command fails with an actionable error and creates no session files

### Requirement: Start reaches a terminal startup condition

`start` SHALL run readiness, dependency, HTTP-port, worker, and reload preflights before spawning Odoo. It SHALL then ensure at most one live session for that environment and start Odoo under debugpy with the recorded interpreter, as `python -m debugpy` followed by the existing Odoo arguments. It SHALL return on the first of: a breakpoint hit, HTTP readiness, target exit, or a 60 second startup timeout.

HTTP readiness without a stop SHALL return state `running`. Zero initial breakpoints SHALL be allowed. A live duplicate start SHALL return `session-already-active`, the current snapshot, and a hint, and SHALL NOT start a second Odoo process.

Missing debugpy, `workers` greater than 0, a reload flag, an invalid breakpoint, or an occupied HTTP port SHALL fail before Odoo spawn with a stable actionable error.

#### Scenario: Ready without a breakpoint

- **WHEN** start is given no breakpoint and the HTTP port becomes ready
- **THEN** the snapshot state is `running` and one Odoo process exists

#### Scenario: Duplicate start

- **WHEN** start is repeated while that session is live
- **THEN** the result is `session-already-active` with the current snapshot and the original Odoo process is unchanged

#### Scenario: Unsafe runtime is refused

- **WHEN** the resolved runtime has `workers` greater than 0, a reload flag, a missing debugpy module, or an occupied HTTP port
- **THEN** start fails before Odoo spawn and leaves no daemon or session

### Requirement: Continue, step, and pause have stable control semantics

`continue` without `--wait` SHALL resume a stopped target and return a `running` snapshot immediately. With `--wait SECONDS` it SHALL resume when stopped, then wait for the next debugger or target event. `--wait` SHALL be a number from 0 exclusive through 300 inclusive. With `-- COMMAND...` it SHALL execute that command directly, without a shell, in the environment runtime directory, and wait for a breakpoint, target exit, trigger exit, or timeout.

When the target is already running, continue SHALL NOT send another DAP continue. It SHALL still apply requested breakpoint changes and MAY launch the trigger. A second reproduction command while one exists SHALL return `trigger-already-running`. A trigger blocked in a breakpoint SHALL stay owned by the session and MAY finish after a later resume.

`context` SHALL return the current snapshot. `context --wait` SHALL wait for the next stop or exit without resuming Odoo. `step over|in|out` SHALL resume and wait for the next stop or exit. `pause` SHALL request a pause and return the stopped snapshot. `eval` SHALL be valid only while stopped. Frame 0 SHALL be the top frame. A non-stopped eval SHALL return `not-stopped`.

Ctrl+C during a wait SHALL cancel only that wait, return exit code 130, and SHALL NOT stop the daemon, Odoo, or an already started reproduction command.

#### Scenario: Running continue is not a second resume

- **WHEN** continue is invoked while the target is already running and no trigger is supplied
- **THEN** no redundant continue is sent and the returned snapshot stays `running`

#### Scenario: Trigger remains across the breakpoint

- **WHEN** a reproduction command hits a breakpoint before it exits
- **THEN** the snapshot shows that trigger as running, and a later resume can let it exit
- **AND** a second reproduction command returns `trigger-already-running`

#### Scenario: Interrupt leaves the session

- **WHEN** the caller interrupts a waiting debug command
- **THEN** that command exits 130 and the daemon, Odoo, and session still exist

### Requirement: Snapshots and values stay bounded and redacted

A snapshot SHALL include schema version, session id, environment id, state, stop reason, location, a short source window, shallow locals, a short stack, the effective breakpoints, output tails, trigger status, and truncation flags. It SHALL NOT include raw DAP identifiers. Paths SHALL be environment-relative when they are inside the runtime directory. Secret and config paths SHALL NOT be returned.

Locals in a snapshot SHALL include at most 32 names. Strings SHALL be truncated at 240 characters. The stack SHALL include at most 20 frames. The source window SHALL include at most 15 lines. Combined stdout and stderr retained for the snapshot SHALL be at most 64 KiB. `eval --depth` SHALL accept only integers 0 through 3 and SHALL be the only expansion control. Depth 0 SHALL be a redacted representation. Each expanded level SHALL include at most 16 children.

Credential-shaped values SHALL be redacted in locals, eval results, output, and diagnostics. A timeout SHALL return `wait-timeout` with the latest snapshot and SHALL leave the session in place.

#### Scenario: Timeout keeps the session

- **WHEN** a bounded wait ends with no debugger event
- **THEN** the error is `wait-timeout`, the payload includes the latest snapshot, and a later context reads the same session

#### Scenario: Secret-shaped local is redacted

- **WHEN** a stopped frame has a local whose name or value is credential-shaped
- **THEN** the snapshot and any eval of that value redact it and do not include the raw secret

### Requirement: Breakpoints update atomically

A breakpoint argument SHALL be `FILE:LINE` or `FILE:LINE:CONDITION`. Parsing SHALL split on at most the first two colons. The path SHALL resolve against the recorded runtime directory and SHALL name an existing file. The line SHALL be a positive integer.

The session SHALL own one logical breakpoint per absolute path and line. `start --break` SHALL set the initial set. `continue --break` SHALL add or replace. `continue --remove-break` SHALL remove. All changes in one command SHALL be validated before any are applied. The debugger SHALL receive the complete set for each affected file. A snapshot SHALL list each entry with verified status and message. `--to FILE:LINE` SHALL add one temporary breakpoint and remove it after the next stop or termination. Breakpoints SHALL survive restart and SHALL NOT survive a completed stop.

#### Scenario: Invalid change applies nothing

- **WHEN** one continue command names a valid breakpoint and a missing file
- **THEN** the command returns `breakpoint-invalid` and the previous logical set is unchanged

#### Scenario: Temporary breakpoint disappears

- **WHEN** continue uses `--to` and the target stops there
- **THEN** the following snapshot no longer lists that temporary breakpoint
- **AND** the other logical breakpoints remain

### Requirement: One daemon owns cleanup

Exactly one daemon and one debug session SHALL exist per environment UUID. Session state SHALL use a directory mode `0700` and a Unix socket mode `0600`. The DAP listener SHALL be loopback-only. Concurrent starts, a stale socket, PID reuse, a client disconnect, and an RPC version mismatch SHALL be deterministic and SHALL NOT start a second Odoo process.

`restart` SHALL terminate the reproduction process group and the current Odoo process group, then launch a new Odoo process with the same session id and logical breakpoints. `stop` SHALL terminate both groups, return a `terminated` snapshot, remove local session state, and exit the daemon. Target exit, daemon crash, and protocol failure SHALL best-effort clean the owned process groups, sockets, process registry, secret config, and artifact lock, and SHALL NOT signal unrelated PIDs.

State-changing and spawning debug operations SHALL expose one immutable command plan of process and action steps. Dry-run SHALL emit that plan and SHALL NOT create a daemon, connect to DAP, spawn a process, or change session state. Normal execution SHALL consume the captured plan.

#### Scenario: Dry-run changes nothing

- **WHEN** any debug verb is invoked with `--dry-run`
- **THEN** the output is one plan document and no daemon, socket, Odoo process, or breakpoint change exists afterward

#### Scenario: Stop removes the session

- **WHEN** stop completes
- **THEN** the daemon, Odoo, reproduction process, socket, and session directory are gone
- **AND** a later start creates a new session id

#### Scenario: Restart keeps identity

- **WHEN** restart completes after breakpoints were set
- **THEN** the session id and logical breakpoints are unchanged and the previous Odoo process id is gone

### Requirement: Eval is trusted local code execution

Help text and the README SHALL state that `eval` executes arbitrary code inside the trusted local Odoo process. The README SHALL also show the agent workflow, `workers=0`, debugpy living in the recorded environment, session persistence, and Ctrl+C behavior. Existing `run`, detached start, `shell`, monitoring, and VS Code profile generation SHALL keep their current behavior apart from the JSONC maintenance change in the CLI spec.

#### Scenario: Documentation names the eval risk

- **WHEN** the debug help and README are read
- **THEN** both warn that eval runs arbitrary code in the local Odoo process
