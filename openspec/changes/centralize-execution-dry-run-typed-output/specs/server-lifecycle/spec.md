## ADDED Requirements

### Requirement: Inspectable Odoo lifecycle commands

`OdooInstance` process-spawning operations SHALL expose command siblings for captured run, background start, foreground run, interactive shell, shell-script execution, and process stop. `stop_command()` SHALL be present with the same public signature and return contract on every supported platform: its plan SHALL contain a captured `ProcessStep` for Windows `taskkill` and an honest `ActionStep` for POSIX signal/no-child termination. Existing methods SHALL delegate without changing return values, process registration, artifact locks, or readiness behavior.

#### Scenario: Stop command is inspected across platforms

- **WHEN** a caller constructs `stop_command()` on Windows or POSIX
- **THEN** the public method exists on both platforms with the same contract
- **AND** Windows plans the `taskkill` child process while POSIX plans the actual signal/no-child action

#### Scenario: Foreground command preserves TTY

- **WHEN** `run_foreground_command()` is run normally
- **THEN** the exact captured process inherits native stdio, owns its process group/session, and retains existing signal/exit behavior

#### Scenario: Shell script command preserves executable input

- **WHEN** `run_shell_script_command()` is inspected
- **THEN** its plan contains the real Odoo shell argv and exact redacted wrapper/source bytes sent through stdin
- **AND** commit or rollback intent is explicit

### Requirement: Lifecycle cleanup remains explicit

Process registration, handle ownership, signal forwarding, TERM/KILL/reap, generated secret-config cleanup, and artifact locking SHALL remain explicit lifecycle code. These effects SHALL NOT be modeled as Expression stages or a generic rollback workflow.

#### Scenario: Foreground wait raises

- **WHEN** waiting for a captured foreground handle raises an exception
- **THEN** the owned process group receives bounded cleanup and is reaped
- **AND** the original exception semantics are retained
