# Design

## Context

Unix `run`, `run --detach`, and `stop` currently spawn Odoo through `internal/proc` and prove ownership with PID, create time, argv, cwd, config, and `pgid == pid`. `odcli monitor` runs Uvicorn inside the CLI process and opens the browser itself. The catalogue runtime row is the only registry. Shell, eval, exec, module tests, and COPY/restore auxiliary Odoo use that same process boundary for finite or auxiliary work and are not long-lived owned runtimes.

GitHub #136 requires one process manager for owned Odoo and the dashboard server. It also requires a comparison with experimental mise daemons before choosing.

## Goals / Non-Goals

**Goals:**

- One private per-user Supervisor for owned Odoo programs and one monitor program.
- Addressable start, stop, and status without `stop all` or a global reload.
- Crash restart while the daemon lives, no start after reboot until the user asks, and no revival after a manual stop.
- Preserve captured argv, dry-run snapshots, secret redaction, and the legacy stop path for rows that have no program yet.

**Non-Goals:**

- mise or pitchfork as a second manager.
- A boot-time launchd or systemd unit.
- Moving shell, tests, or auxiliary restore onto Supervisor.
- New monitor API features, Windows Job Objects, or adoption of a foreign PID.
- Claiming reaping of descendants that left the program process group.

## Decisions

### Supervisor, not mise daemons

mise daemons are an experimental alternative manager built on pitchfork, not a layer under Supervisor. OdCLI runtimes are catalogue owners (environment or project) plus one user-level monitor, not mise worktrees. The required contracts are a private Unix socket, per-program `update`/`start`/`stop` without touching other programs, `autostart=false`, `autorestart=unexpected`, group stop, and control while HTTP is down. Those are native Supervisor behaviors. Meeting them on mise would mean a second daemon, a second naming scheme, and workarounds for socket ownership and "do not autostart after reboot". #136 says to keep Supervisor when mise cannot meet the contracts without workarounds. This change does that, and records the comparison here instead of adding a throwaway mise prototype to the product tree.

The dependency is the PyPI `supervisor` package, Unix only: `supervisor>=4.2.5,<5; sys_platform != 'win32'`. The daemon and client are `python -m supervisor.supervisord` and `python -m supervisor.supervisorctl` with `shell=False`, launched only through `internal/proc`.

### One home under the OdCLI root

`~/.odcli/supervisor` (honoring `ODCLI_USER_ROOT`) holds `supervisord.conf`, the socket, the pidfile, the daemon log, and `programs/*.conf`. Directories are mode `0700` and files are mode `0600`. Planning computes these paths without creating them. Execution creates them. The socket path is the only server URL. A healthy socket is reused; otherwise the daemon is started in the background.

Program names are `odoo-environment-<id>`, `odoo-project-<id>`, and `odcli-monitor`, with characters outside `[A-Za-z0-9_-]` replaced. The name is derived from the owner, so stop does not need a new catalogue column.

Each program sets `autostart=false`, `autorestart=unexpected`, `startretries=3`, `stopsignal=TERM`, `stopasgroup=true`, and `killasgroup=true`. `stopwaitsecs` follows the stop timeout. After reboot nothing runs until a managed command starts the daemon, and `autostart=false` keeps previously stopped programs stopped. An unexpected exit restarts only while that daemon process is alive.

### Command snapshot

The Odoo argv remains the first captured process step so dry-run and launch identity keep today's command. Unix plans also include action `write_supervisor_program` and process steps for daemon, `update`, `start`, and `status`. Foreground adds `tail -f`. Execution of the real `SubprocessExecutor` skips the direct Odoo spawn and runs the Supervisor steps from that snapshot. The recording executor used by existing unit tests skips the Supervisor steps and keeps the direct spawn, because that double cannot host a daemon. Both paths consume every planned step. Production tests start a real temporary daemon.

Stop adds `supervisorctl stop <program>` only when that owner's program file exists. Otherwise the current signal path remains for pre-Supervisor rows.

Foreground holds the artifact lock only while registering the program and the runtime row, then follows logs. Ctrl-C returns 130 and does not stop the program or delete the row. A parallel `odcli stop` stops the program because the lock is not held during the follow.

`RUNNING` is liveness only. Opt-in `--wait-ready` still uses the health probe. Readiness failure stops the program this launch started.

### Monitor

`odcli monitor` becomes a group. With no subcommand, or with `start`, it registers `odcli-monitor` only after the dashboard extra imports. The program command is `python -m odoo_instance_sdk.internal.monitor_program` and does not open a browser. The CLI opens the browser after `GET /healthz` unless `--headless` or `--no-open`. `--detach` returns after that check. Ctrl-C detaches.

The saved endpoint lives in `monitor.json` mode `0600`. A later start reuses it. A requested address that differs while the program is running is an error. A new address is written only when the program is stopped and the user passed `--host` or `--port`. An occupied saved port fails without a signal and without picking another port.

`stop`, `restart`, `status`, and `logs` are bounded SDK commands. They do not require HTTP. `restart` is `supervisorctl restart` of that one program and does not reread a changed command; address changes go through an explicit stopped start.

### Windows

Managed run, Supervisor-owned stop, and monitor raise one error: Supervisor-managed lifecycle is not supported on Windows. Legacy rows without a program still use the existing stop path.

## Risks / Trade-offs

- Foreground Ctrl-C no longer kills Odoo. The help text and the exit-130 result say the program is still running.
- Existing unit tests keep a recording-executor path so identity and argv contracts stay pinned. A real daemon test covers the production path; reviewers should treat only that path as the lifecycle proof.
- `stopasgroup` does not affect children that left the process group. The docs say so.
- No login or boot service. A reboot stops everything until the next `odcli run` or `odcli monitor`.
