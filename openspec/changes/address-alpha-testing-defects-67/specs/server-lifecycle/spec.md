## ADDED Requirements

### Requirement: Detached Odoo launch via run_foreground

`OdooInstance` SHALL expose a detached launch mode that spawns Odoo, confirms the process is alive, persists runtime identity, and returns without waiting for Odoo to finish. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, process executor, runtime identity, and ownership/stop mechanism. A separate daemon manager or second command-construction path SHALL NOT be introduced.

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