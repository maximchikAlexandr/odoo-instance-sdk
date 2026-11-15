# Proposal

## Why

OdCLI still owns long-running Odoo and the dashboard monitor by spawning them directly and guessing ownership from PID, create time, and port. That ownership breaks across CLI restarts and cannot restart a crashed process without a second daemon. GitHub #136 asks for one user-scoped process manager for those two programs, and the owner has now authorized the OpenSpec change.

## What Changes

- Install Supervisor with OdCLI and run one dedicated user `supervisord`, reached only through `supervisorctl` and a private Unix socket. Do not attach to a system Supervisor or manage foreign programs.
- Register each project-owned and environment-owned Odoo runtime as its own foreground Supervisor program, using the captured executable, argv, cwd, and config. `run --detach` starts only that program. Supervisor `RUNNING` is not HTTP readiness.
- **BREAKING:** Unix foreground `odcli run` also starts that program and then follows its log. Ctrl-C detaches the client and leaves Odoo running; `odcli stop` is how the user stops it. Windows managed `run`, `stop`, and `monitor` fail with an explicit unsupported message.
- Stop and the existing opt-in restart address only the saved program. A repeated stop of an already stopped program succeeds. Legacy runtimes that were launched before Supervisor remain stoppable by the current identity-checked signal path. Shell, tests, and COPY/restore auxiliary Odoo stay outside Supervisor.
- Run the dashboard Uvicorn server as a separate program of the same `supervisord`. Add addressable start, stop, restart, status, and logs on the monitor surface. Those commands talk to Supervisor even when the HTTP API is down. A monitor crash restarts on the saved address; a manual stop stays stopped; Odoo and the monitor do not stop each other.
- Open the browser only from the user CLI after `/healthz`, and only when that invocation asked for a browser. The supervised process never opens one. A core install without the dashboard extra does not register the monitor.

## Capabilities

### New Capabilities

- `supervisor-runtime`: User-scoped Supervisor lifecycle, program identity, crash policy, socket permissions, and the monitor program.

### Modified Capabilities

- `server-lifecycle`: Unix detached and foreground Odoo launches go through the captured Supervisor program instead of a direct long-running spawn. Foreground Ctrl-C detaches.
- `instance-runtime-binding`: Stop of a Supervisor-managed runtime addresses the saved program. Pre-Supervisor rows keep the existing identity-checked stop.
- `cli-odcli`: `odcli run`, `odcli stop`, and `odcli monitor` expose the Supervisor behavior, including monitor start/stop/restart/status/logs and the Windows limitation.

## Impact

- New process-manager dependency on Unix: the PyPI `supervisor` package. No Go daemon and no mise/pitchfork dependency.
- Launch and stop planning in `resources/instance`, the monitor CLI, `~/.odcli/supervisor`, docs, and the CLI leaf inventory.
- Public `run` / `run --detach` / `stop` command names stay. Foreground Ctrl-C and Windows managed lifecycle behavior change as marked above.
- COPY/restore, `shell`, `eval`, `exec`, and module tests are unchanged.
