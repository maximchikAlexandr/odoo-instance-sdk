## ADDED Requirements

### Requirement: `server.start()` launches a long-lived process

`server.start(config: StartConfig, *, cwd: str | Path | None = None, env: dict[str, str] | None = None)` SHALL launch the Odoo executable as a long-lived subprocess using `[executable, *cli_args]` derived from `StartConfig` fields. The method SHALL return immediately with an `OdooProcess` instance. SDK SHALL own only processes it started through this resource.

#### Scenario: Start returns a process object
- **WHEN** `server.start(StartConfig(http_port=8069))` is called
- **THEN** an `OdooProcess` is returned
- **AND** the process is running
- **AND** the process is registered in the client's registry

### Requirement: `StartConfig` is strongly typed

`StartConfig` (msgspec.Struct) SHALL expose typed fields corresponding to Odoo 19.0 CLI options needed to launch an HTTP server (the exact field set is enumerated in design.md D2; literal-typed where applicable, e.g. `log_level`). SDK SHALL NOT accept unknown fields; instantiation with the wrong type for any field SHALL raise a typed error (`ConfigError`).

#### Scenario: Wrong type for port raises
- **WHEN** user attempts to construct `StartConfig(http_port="8069")` (str instead of int)
- **THEN** a typed `ConfigError` is raised

#### Scenario: Valid config is accepted
- **WHEN** `StartConfig(http_port=8069, log_level="info")` is constructed
- **THEN** all fields are accessible as typed attributes

### Requirement: `OdooProcess` fields

`OdooProcess` (msgspec.Struct) SHALL contain:
- `id: str` — internal identifier (`uuid4().hex`)
- `pid: int` — OS process id
- `args: list[str]` — full argument list (including executable)
- `started_at: float` — monotonic or wall-clock timestamp of start

#### Scenario: Process fields are populated
- **WHEN** `start()` returns
- **THEN** `proc.id` is a 32-char hex string
- **AND** `proc.pid > 0`
- **AND** `proc.args[0] == <executable>`
- **AND** `proc.started_at > 0`

### Requirement: Process registry is per-client

The registry SHALL live on the `OdooClient` instance. A process started by one client SHALL NOT be visible or controllable via another client's `ServerResource`, even in the same Python process.

#### Scenario: Cross-client isolation
- **WHEN** client A starts a process and client B calls `server.status(proc_a)`
- **THEN** `ProcessNotFoundError` is raised

### Requirement: `server.stop()` performs graceful then forced termination

`server.stop(proc: OdooProcess, *, timeout: float = 10.0)` SHALL:
1. Send a graceful termination signal to the process group (POSIX: `SIGTERM` to pgid; Windows: `taskkill /T /PID <pid>`).
2. Wait up to `timeout` seconds for the process to exit.
3. If it does not exit within `timeout`, force-kill the group (POSIX: `SIGKILL` to pgid) and wait again.

#### Scenario: Graceful stop on POSIX
- **WHEN** `server.stop(proc)` is called on a running process
- **THEN** `SIGTERM` is sent to the process's group
- **AND** if it exits within `timeout`, no `SIGKILL` is sent

#### Scenario: Forced kill on timeout
- **WHEN** the process does not exit within `timeout`
- **THEN** `SIGKILL` is sent to the process group
- **AND** the method waits for the process to actually exit

### Requirement: `stop()` is idempotent on already-terminated processes

Calling `stop()` on an already-terminated registered process SHALL NOT raise. If the OS reports the PID does not exist (`ESRCH`), the SDK SHALL treat it as already-stopped and return silently.

#### Scenario: Double stop does not raise
- **WHEN** `server.stop(proc)` is called twice on the same process
- **THEN** the second call returns normally without raising

### Requirement: `server.status()` returns typed status

`server.status(proc: OdooProcess) -> ProcessStatus` SHALL return a typed value exposing:
- `state: Literal["running", "exited"]`
- `returncode: int | None` — `None` while running, integer once exited (may be `None` briefly even after exit if not yet reaped)

Passing an unregistered `OdooProcess` SHALL raise `ProcessNotFoundError`.

#### Scenario: Status of running process
- **WHEN** `server.status(proc)` is called on a running registered process
- **THEN** `status.state == "running"`
- **AND** `status.returncode is None`

#### Scenario: Status of exited process
- **WHEN** the registered process has exited and been reaped
- **THEN** `status.state == "exited"`
- **AND** `status.returncode` is an `int`

#### Scenario: Status of unknown process raises
- **WHEN** `server.status(unknown_proc)` is called where `unknown_proc.id` is not in the registry
- **THEN** `ProcessNotFoundError` is raised

### Requirement: Multiple `start()` calls are allowed

A single `OdooClient` SHALL permit multiple concurrent `start()` calls. Each gets its own `id`, `pid`, and registry entry. There is no implicit coupling between processes started on the same client.

#### Scenario: Two concurrent processes
- **WHEN** `start()` is called twice with configs binding to different ports
- **THEN** two distinct `OdooProcess` objects are returned
- **AND** `p1.id != p2.id`
- **AND** `p1.pid != p2.pid`