# Spec Delta

## MODIFIED Requirements

### Requirement: Detached Odoo launch

`OdooInstance` SHALL expose a detached launch command sibling that spawns Odoo through `internal/proc`, confirms the process is alive, persists runtime identity, and returns without waiting for Odoo to finish. The convenience method SHALL delegate to that captured command and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Detached launch SHALL NOT overload `run_foreground_command()`, which continues to inherit stdio and block until Odoo exits. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, `internal/proc`, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL return PID, project/environment identity, HTTP endpoint, and log path. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting the launcher SHALL NOT terminate the child. Stopping SHALL work through the existing `stop()` and stale runtime records SHALL be recognized normally.

When structured Odoo logging is off, logs SHALL be written to the path from `resolve_effective_logfile()`. When it is on, the same captured command SHALL pass one run-specific `--logfile` override and SHALL NOT also write the environment `odoo.log` or a second config copy. The run directory and logfile SHALL be created only during execution, with directory mode `0700` and logfile mode `0600`. An unwritable fallback SHALL fail before spawn with `logfile_unwritable` and the exact path. `iter_logs` SHALL read the effective raw logfile. `--dry-run` SHALL show the sanitized command, detached plan, and logfile path without spawning or creating the run directory or file. Configured level and handlers SHALL be applied to the new run through the existing `log_level` and `log_handler` arguments.

#### Scenario: Detached launch returns promptly
- **WHEN** a detached launch succeeds
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success
- **WHEN** the detached Odoo process exits immediately after spawn
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime
- **WHEN** `stop()` runs after a detached launch
- **THEN** it matches the launch argv including the injected effective `--logfile` and stops exactly that persisted runtime

#### Scenario: Logs work after detached launch
- **WHEN** `iter_logs(tail=...)` and `iter_logs(follow=True)` run after a detached launch
- **THEN** both read from the resolved logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn
- **WHEN** a detached dry-run is requested
- **THEN** the sanitized process command, detached lifecycle plan, and resolved logfile path are shown and no process, runtime record, directory, or file is created

#### Scenario: No logfile fails fast
- **WHEN** a detached launch is requested with no configured logfile and structured logging is off
- **THEN** the command fails before spawn with a clear diagnostic

#### Scenario: Detached launch provisions an effective logfile
- **WHEN** a detached launch is requested with no configured `logfile` and structured logging is off
- **THEN** `odoo.log` next to the effective config is created before spawn and used, without editing the user's `odoo.conf`

#### Scenario: Unwritable logfile fails fast
- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before spawn with a clear diagnostic and the exact path

#### Scenario: Structured dry-run keeps the run directory absent
- **WHEN** structured logging is on and a detached dry-run is requested
- **THEN** the plan shows one run-specific logfile argument and neither the run directory nor the logfile is created

## ADDED Requirements

### Requirement: Structured run metadata
A structured Odoo run SHALL record non-secret metadata containing the run id, environment id, branch, commit SHA, start time, end time, exit code, root PID, process create time when runtime identity is available, and state `running`, `finished`, `interrupted`, or `stale`. The metadata file SHALL be mode `0600` and SHALL NOT contain credentials or a copy of `odoo.conf`. Foreground and detached launches SHALL share this behavior. End time and exit code MAY be empty while the run is active and SHALL be recorded when the SDK observes a finished or interrupted run.

#### Scenario: Structured run creates private metadata
- **WHEN** a structured run starts
- **THEN** the run directory is mode `0700`, the logfile and metadata are mode `0600`, and the metadata identifies that run without secrets

#### Scenario: Active run has no exit code yet
- **WHEN** metadata is read while the run is still active
- **THEN** the state is `running` and the exit code is empty

### Requirement: Structured event iterator
`OdooInstance` SHALL expose one event iterator for structured Odoo logs. It SHALL accept the run selector, text, levels, logger prefix, database, since, until, after cursor or mark, until cursor, limit, and follow. The CLI SHALL use that iterator and SHALL NOT reimplement parsing, filtering, or cursor movement. Cursor and mark result models SHALL match the JSON contract. The iterator SHALL NOT be available as a substitute for raw `iter_logs()` when structured logging is off.

#### Scenario: SDK and CLI share one page
- **WHEN** a CLI search and the instance iterator use the same filters and cursors
- **THEN** they return the same events and the same next and window cursors

#### Scenario: Raw logs stay raw
- **WHEN** structured logging is off and the caller uses `iter_logs()`
- **THEN** the result is raw text from the effective logfile and no event parser runs
