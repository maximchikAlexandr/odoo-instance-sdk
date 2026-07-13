## ADDED Requirements

### Requirement: `server.run()` executes one CLI command

`server.run(args: list[str], *, cwd: str | Path | None = None, env: dict[str, str] | None = None, timeout: float | None = None)` SHALL execute `[executable, *args]` via `subprocess` without using shell. SDK SHALL NOT modify, reorder, or interpret the contents of `args`; they are passed to Odoo verbatim.

#### Scenario: Run with positional args
- **WHEN** `server.run(["--version"])` is called
- **THEN** the executed command list is `[<executable>, "--version"]`
- **AND** no shell is invoked

### Requirement: `CommandResult` fields

A successful `server.run()` SHALL return a `CommandResult` (msgspec.Struct) containing:
- `args: list[str]` — full argument list (including executable)
- `returncode: int`
- `stdout: str`
- `stderr: str`
- `duration: float` — execution time in seconds

#### Scenario: Successful command result
- **WHEN** a command exits with code 0 and stdout `"17.0\n"`
- **THEN** `result.returncode == 0`
- **AND** `result.stdout == "17.0\n"`
- **AND** `result.args[0] == <executable>`
- **AND** `result.duration >= 0.0`

### Requirement: `cwd` and `env` are honoured

When `cwd` is provided, the subprocess SHALL use it as working directory. When `env` is provided, the subprocess SHALL use it as its environment (replacing the parent's). When both are `None`, parent's cwd and env are inherited.

#### Scenario: Custom working directory
- **WHEN** `server.run(["--version"], cwd="/tmp")` is called
- **THEN** the subprocess executes with cwd `/tmp`

#### Scenario: Custom environment
- **WHEN** `server.run(["--version"], env={"PATH": "/usr/bin"})` is called
- **THEN** the subprocess environment is exactly `{"PATH": "/usr/bin"}`

### Requirement: Timeout behaviour

When `timeout` is provided and exceeded, the SDK SHALL terminate the process (including any child processes in the same process group) and raise `CommandTimeoutError`. The error SHALL be a typed exception; its message SHALL NOT leak `master_pwd` (it never appears in `run()` regardless).

#### Scenario: Timeout raises typed error
- **WHEN** `server.run(["--help"], timeout=0.001)` is called and the process exceeds the timeout
- **THEN** the process is terminated
- **AND** `CommandTimeoutError` is raised

#### Scenario: No timeout waits indefinitely
- **WHEN** `server.run(long_running_args)` is called without `timeout`
- **THEN** the method blocks until the process exits naturally