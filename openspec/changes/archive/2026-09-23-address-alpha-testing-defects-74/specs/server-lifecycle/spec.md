## ADDED Requirements

### Requirement: Long-restore Rich stages and elapsed time

Long-running restore SHALL publish stages through existing `StepObserver`/`StepEvent` and Rich Live: ids `backup_prepare`, `auxiliary_start`, `db_restore`, `db_verify`, `filestore_restore`, `admin_reset`, `default_switch`. Heartbeat SHALL emit `StepEvent` every 0.5 s without child stdout. Percent SHALL be shown only when streaming dump/filestore bytes provide a total. On error, the safe primary cause, last stage id, and elapsed seconds SHALL be preserved. JSON/TOON SHALL keep one document; no heartbeat events on machine stdout.

#### Scenario: Current stage appears immediately

- **WHEN** a long restore starts in Rich
- **THEN** the current stage appears immediately after the operation begins

#### Scenario: Spinner and elapsed time update without child stdout

- **WHEN** a long blocking stage is in progress
- **THEN** the spinner and elapsed time update without child stdout

#### Scenario: Percent only with a trustworthy total

- **WHEN** streaming dump/filestore bytes provide a total
- **THEN** percent is shown; without a total, only spinner and elapsed time are shown

#### Scenario: Error preserves primary cause and last stage

- **WHEN** a restore fails
- **THEN** the safe primary cause, last stage id, and elapsed seconds are preserved instead of a generic readiness message

#### Scenario: Machine output is unchanged

- **WHEN** a long restore runs in JSON or TOON
- **THEN** the single-document contract and exit code are preserved and terminal progress does not pollute machine stdout

### Requirement: Auxiliary Odoo stdout/stderr are drained concurrently

A spawned long-running auxiliary Odoo handle SHALL be drained by the `command-execution` process pump (`inherit_stdio=False`, 8192-byte redacted tail). This requirement SHALL NOT duplicate that contract.

#### Scenario: noisy auxiliary child does not block

- **WHEN** an auxiliary child writes more than the pipe capacity
- **THEN** it does not block and passes readiness/cleanup

#### Scenario: simultaneous drain with bounded memory

- **WHEN** both `stdout` and `stderr` are active
- **THEN** they are drained simultaneously and the tail memory is bounded

#### Scenario: readiness failure contains primary cause and tail

- **WHEN** readiness/startup fails
- **THEN** the failure contains the primary safe cause and a bounded redacted tail, not a single generic message

#### Scenario: cleanup terminates readers and process group

- **WHEN** cleanup or interrupt runs
- **THEN** the child process group and readers are terminated with no leaked threads or handles

### Requirement: `OdooInstance.run_foreground_command()`

`OdooInstance.run_foreground_command()` SHALL launch the resolved Odoo runtime in the foreground. The convenience `run_foreground()` SHALL only delegate to that captured command. Expression SHALL NOT appear in lock acquire/release, spawn, wait, cleanup, or compensation. The command SHALL enter the shared artifact lock only for the atomic spawn and runtime-identity registration, then release the lock before `wait_foreground_process()`. On exit, it SHALL re-acquire that same shared artifact lock for cleanup/revalidation. The wait SHALL NOT hold the shared artifact lock, so a parallel `stop` can acquire the exclusive lock, read the persisted runtime identity, and call the existing `terminate_pid()`. Foreground stdio, signals, exit code, PID/create-time/process-group validation, and stale-runtime safety SHALL be preserved.

#### Scenario: Foreground run does not hold the lock while waiting

- **WHEN** `run_foreground_command()` is waiting on the foreground process
- **THEN** the shared artifact lock is not held and a parallel `stop` can acquire the exclusive lock

#### Scenario: Live E2E parallel stop

- **WHEN** a live E2E runs real `odcli run` in one CLI session and real `odcli stop` in a second while the first is waiting
- **THEN** `stop` succeeds, the registered process group terminates, the HTTP port is released, and runtime identity is cleaned up

#### Scenario: Parallel stop terminates the foreground runtime

- **WHEN** a parallel `stop` runs while `run_foreground_command()` is waiting
- **THEN** `stop` acquires the exclusive lock, reads the persisted runtime identity, and terminates the registered runtime

#### Scenario: Foreground stdio and exit code are preserved

- **WHEN** `run_foreground_command()` runs normally, exits non-zero, or is interrupted
- **THEN** inherited stdin/stdout/stderr remain native, the real exit code is returned, and interrupt cleanup preserves exit `130`

## MODIFIED Requirements

### Requirement: Detached Odoo launch

`OdooInstance` SHALL expose a detached launch command sibling that spawns Odoo through `internal/proc`, confirms the process is alive, persists runtime identity, and returns without waiting for Odoo to finish. The convenience method SHALL delegate to that captured command and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Detached launch SHALL NOT overload `run_foreground_command()`, which continues to inherit stdio and block until Odoo exits. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, `internal/proc`, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

After spawn, the command SHALL return PID, project/environment identity, HTTP endpoint, and log path. If the process exits immediately, the command SHALL return an error and SHALL NOT leave a false `running` record. The Odoo lifetime SHALL NOT be tied to the terminal or CLI: exiting the launcher SHALL NOT terminate the child. Stopping SHALL work through the existing `stop()` and stale runtime records SHALL be recognized normally.

Logs SHALL be written to the path from `resolve_effective_logfile()` in `resources/instance/runtime.py` (see `cli-odcli` `odcli run`). An unwritable fallback SHALL fail before spawn with `logfile_unwritable` and the exact path. `iter_logs` SHALL read that path. `--dry-run` SHALL show the sanitized command, detached plan, and logfile path without spawning or creating the file.

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
- **THEN** both read from the resolved logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** a detached dry-run is requested
- **THEN** the sanitized process command, detached lifecycle plan, and resolved logfile path are shown and no process, runtime record, directory, or file is created

#### Scenario: No logfile fails fast

- **WHEN** a detached launch is requested with no configured logfile
- **THEN** the command fails before spawn with a clear diagnostic

#### Scenario: Detached launch provisions an effective logfile

- **WHEN** a detached launch is requested with no configured `logfile`
- **THEN** `odoo.log` next to the effective config is created before spawn and used, without editing the user's `odoo.conf`

#### Scenario: Unwritable logfile fails fast

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before spawn with a clear diagnostic and the exact path

