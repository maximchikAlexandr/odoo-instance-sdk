## ADDED Requirements

### Requirement: Detached Odoo launch

`OdooInstance` SHALL expose a detached launch command sibling that spawns Odoo through `internal/proc`, confirms the process is alive, persists runtime identity, and returns without waiting for Odoo to finish. The convenience method SHALL delegate to that captured command and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Detached launch SHALL NOT overload `run_foreground()`, which continues to inherit stdio and block until Odoo exits. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, process executor, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL return PID, project/environment identity, HTTP endpoint, and log path. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting the launcher SHALL NOT terminate the child. Stopping SHALL work through the existing `stop()` and stale runtime records SHALL be recognized normally.

Logs SHALL be written to the existing `logfile` of the bound `odoo.conf`. When no logfile is configured, the command SHALL fail fast before spawn with a clear diagnostic; stdout/stderr SHALL NOT be lost and a parallel log store SHALL NOT be created. `iter_logs(tail=...)` and `iter_logs(follow=True)` SHALL work after detached launch. `--dry-run` SHALL show the exact sanitized Odoo process command and detached lifecycle plan without spawning.

#### Scenario: Detached launch returns promptly

- **WHEN** a detached launch succeeds
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success

- **WHEN** the detached Odoo process exits immediately after spawn
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime

- **WHEN** `stop()` runs after a detached launch
- **THEN** it stops exactly that persisted runtime

#### Scenario: Logs work after detached launch

- **WHEN** `iter_logs(tail=...)` and `iter_logs(follow=True)` run after a detached launch
- **THEN** both read from the bound logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** a detached dry-run is requested
- **THEN** the sanitized process command and detached lifecycle plan are shown and no process or runtime record is created

#### Scenario: No logfile fails fast

- **WHEN** a detached launch is requested with no configured logfile
- **THEN** the command fails before spawn with a clear diagnostic

## MODIFIED Requirements

### Requirement: Inspectable Odoo lifecycle commands

`OdooInstance` process-spawning operations SHALL expose command siblings for captured run, background start, foreground run, detached run, interactive shell, shell-script execution, and process stop. `stop_command()` SHALL be present with the same public signature and return contract on every supported platform: its plan SHALL contain a captured `ProcessStep` for Windows `taskkill` and an honest `ActionStep` for POSIX signal/no-child termination. Existing methods SHALL delegate without changing return values, process registration, artifact locks, or readiness behavior. The detached launch convenience method SHALL delegate to its `*_command()` sibling and SHALL NOT rebuild argv, cwd, environment, stdin, or actions.

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

#### Scenario: Detached command is inspectable

- **WHEN** a caller constructs the detached launch command
- **THEN** the captured plan includes the sanitized process command and detached lifecycle actions without spawning
- **AND** the convenience method delegates to that command without reconstructing argv

### Requirement: Foreground lifecycle persists either owner kind

`OdooInstance.run_foreground_command()` SHALL use the single runtime binding attached by `from_environment()` or `from_project()` to persist and clear the current process identity. Detached launch SHALL use that same binding to persist identity after spawn and SHALL leave it in place until `stop()` or stale-runtime recognition; returning from the detached launcher SHALL NOT clear the record. This SHALL remain explicit process-lifecycle code and SHALL not apply to manual instances, shell, shell-script, background `start()`, or the stop operation itself.

#### Scenario: Project and environment use one lifecycle

- **WHEN** equivalent foreground commands start from project-owned and environment-owned instances
- **THEN** both use the same spawn/cleanup path and differ only in the exclusive persisted owner identity

#### Scenario: Detached launch persists the same binding

- **WHEN** a detached launch succeeds from a project-owned or environment-owned instance
- **THEN** the same runtime binding is persisted and remains after the launcher returns