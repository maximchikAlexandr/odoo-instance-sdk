# Spec Delta

## MODIFIED Requirements

### Requirement: Detached Odoo launch

`OdooInstance` SHALL expose a detached launch command sibling that starts the captured Odoo command as the caller's own Supervisor program, confirms that program stayed up, persists runtime identity, and returns without waiting for Odoo to finish. The convenience method SHALL delegate to that captured command and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Detached launch SHALL NOT overload `run_foreground_command()`. The detached mode SHALL use the existing project/environment resolution, port preflight, PostgreSQL preflight, argv construction, runtime identity, and the private Supervisor. Shell, tests, and auxiliary restore Odoo SHALL NOT be moved onto Supervisor.

After start, the command SHALL return the program PID, project/environment identity, HTTP endpoint, and log path. Supervisor state `RUNNING` SHALL NOT be treated as HTTP readiness. If the program enters `BACKOFF` or `FATAL`, or the daemon is unreachable, the command SHALL return an error and SHALL NOT leave a false running record. The Odoo lifetime SHALL NOT be tied to the terminal: exiting the launcher SHALL NOT terminate the program. A second start of the same runtime SHALL reuse the running program. Stopping SHALL address that program, and stale runtime records SHALL be recognized normally.

Logs SHALL be written to the path from `resolve_effective_logfile()` in `resources/instance/runtime.py` (see `cli-odcli` `odcli run`). An unwritable fallback SHALL fail before start with `logfile_unwritable` and the exact path. `iter_logs` SHALL read that path. `--dry-run` SHALL show the sanitized command, the Supervisor actions, and the logfile path without spawning or creating the file.

#### Scenario: Detached launch returns promptly

- **WHEN** a detached launch succeeds
- **THEN** the command returns promptly with PID, identity, endpoint, and log path while Odoo continues running

#### Scenario: Immediate exit is not success

- **WHEN** the detached program enters `BACKOFF` or `FATAL` or exits immediately after start
- **THEN** the command returns an error and leaves no stale active runtime record

#### Scenario: Stop targets the detached runtime

- **WHEN** `stop()` runs after a detached launch
- **THEN** it matches the launch argv including the injected effective `--logfile` and stops exactly that persisted program

#### Scenario: Logs work after detached launch

- **WHEN** `iter_logs(tail=...)` and `iter_logs(follow=True)` run after a detached launch
- **THEN** both read from the resolved logfile and follow produces new lines

#### Scenario: Detached dry-run does not spawn

- **WHEN** a detached dry-run is requested
- **THEN** the sanitized process command, Supervisor lifecycle plan, and resolved logfile path are shown and no process, runtime record, directory, or file is created

#### Scenario: No logfile fails fast

- **WHEN** a detached launch is requested with no configured logfile
- **THEN** the command fails before start with a clear diagnostic

#### Scenario: Detached launch provisions an effective logfile

- **WHEN** a detached launch is requested with no configured `logfile`
- **THEN** `odoo.log` next to the effective config is created before start and used, without editing the user's `odoo.conf`

#### Scenario: Unwritable logfile fails fast

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before start with a clear diagnostic and the exact path

#### Scenario: Running state is not readiness

- **WHEN** a detached launch reports the program `RUNNING` before Odoo answers HTTP
- **THEN** the launch result does not claim HTTP readiness

### Requirement: `OdooInstance.run_foreground_command()`

`OdooInstance.run_foreground_command()` SHALL launch the resolved Odoo runtime through the same private Supervisor program as detached launch, then follow that program's output until the client detaches or the program exits. The convenience `run_foreground()` SHALL only delegate to that captured command. Expression SHALL NOT appear in lock acquire/release, start, follow, cleanup, or compensation. The command SHALL enter the shared artifact lock only for program registration and runtime-identity registration, then release the lock before following output. On client Ctrl-C, the command SHALL return exit `130`, SHALL leave the program running, and SHALL keep the runtime row. When the program itself exits, it SHALL re-acquire the shared artifact lock and clear the matching runtime row. The follow SHALL NOT hold the shared artifact lock, so a parallel `stop` can acquire the exclusive lock and stop the program. Foreground output SHALL be the program log stream. Windows SHALL fail with the unsupported-Supervisor error before start.

#### Scenario: Foreground run does not hold the lock while waiting

- **WHEN** `run_foreground_command()` is following the program log
- **THEN** the shared artifact lock is not held and a parallel `stop` can acquire the exclusive lock

#### Scenario: Live E2E parallel stop

- **WHEN** a live E2E runs real `odcli run` in one CLI session and real `odcli stop` in a second while the first is following logs
- **THEN** `stop` stops that program, the HTTP port is released, and runtime identity is cleaned up

#### Scenario: Parallel stop terminates the foreground runtime

- **WHEN** a parallel `stop` runs while `run_foreground_command()` is following logs
- **THEN** `stop` acquires the exclusive lock and stops the registered program

#### Scenario: Foreground stdio and exit code are preserved

- **WHEN** `run_foreground_command()` is interrupted with Ctrl-C
- **THEN** the client exits `130`, the program log stream stops following, the Odoo program keeps running, and the runtime row remains

### Requirement: Environment-bound detached launch supports readiness waiting

The public detached launch operation MUST accept opt-in readiness waiting and a finite positive timeout that defaults to 60 seconds when enabled. Supplying a readiness timeout while waiting is disabled MUST fail validation. The immutable command snapshot MUST represent readiness and failure cleanup as actions and MUST reuse the private Supervisor program, persisted runtime identity, and health probe. The program command MUST retain the injected effective `--logfile`. Auxiliary restore SHALL keep its own process handling and SHALL NOT move onto Supervisor.

For an adopted independent clone, runtime binding SHALL use the configured project's identity and Git common directory while matching the exact environment owner row and its recorded checkout. Readiness polling SHALL first prove the persisted program is still the started program without requiring a listener that has not started yet, keep that proof during HTTP polling, and prove the listener belongs to that program after the health probe succeeds. A response from another process SHALL never establish readiness. Supervisor `RUNNING` alone SHALL NOT establish readiness.

#### Scenario: Detached launch becomes ready

- **WHEN** readiness waiting is enabled for an environment-bound launch and the program remains up with the expected environment/database binding until the health probe succeeds
- **THEN** the operation returns the established bounded detached result with readiness confirmed

#### Scenario: Adopted independent clone becomes ready

- **WHEN** an adopted environment runs from an independent clone whose Git common directory differs from its configured project's Git common directory
- **THEN** the runtime is recorded under the configured project and exact environment owner, the command waits while the program starts listening, and success requires both a passing health probe and listener ownership by that same program

#### Scenario: Readiness fails

- **WHEN** the program exits early, has an invalid binding, or misses the readiness deadline
- **THEN** the operation stops only the program it started, clears its runtime identity, and returns the established typed error
- **AND** preserves the environment, database, logfile and bounded process-output evidence

#### Scenario: Failure cleanup cannot stop the process

- **WHEN** readiness fails and stopping the program cannot be confirmed
- **THEN** the error reports the surviving owned program and cleanup failure
- **AND** its recovery identity is retained instead of falsely reporting successful cleanup

#### Scenario: Another process occupies the endpoint

- **WHEN** the health endpoint responds but belongs to a different process or database binding
- **THEN** launch is not reported ready and no unrelated process is terminated

#### Scenario: Readiness is disabled

- **WHEN** a caller performs detached launch without readiness waiting
- **THEN** existing launch behavior and result compatibility remain unchanged
