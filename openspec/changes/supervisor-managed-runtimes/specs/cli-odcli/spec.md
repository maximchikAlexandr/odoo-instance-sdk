# Spec Delta

## MODIFIED Requirements

### Requirement: `odcli run`

`odcli run` SHALL launch the resolved Odoo runtime from either a ready environment or an initialized project through the private Supervisor program. For project context, it SHALL derive the Python executable, Odoo entry point, source Odoo config, runtime working directory, preferred HTTP port, default database, default run arguments, and project PostgreSQL binding from `.odcli/project.toml` and the referenced config. Missing required runtime fields or files SHALL fail before process construction with a sanitized actionable error.

The command SHALL preserve the existing literal `--` delimiter rule, exact passthrough argument order, protected runtime-identity validation, free-port preflight, and dry-run rendering. Project context has no environment use metadata, so it SHALL NOT call `EnvironmentResource.record_use()`; environment context SHALL retain its existing record-use behavior after successful preflight and before execution. On Unix, foreground run SHALL follow the program log and SHALL return exit `130` on Ctrl-C without stopping the program. `odcli stop` SHALL stop it. On Windows the command SHALL fail with the unsupported-Supervisor message before launch.

The command SHALL accept `-d, --detach` to launch Odoo detached. Detach SHALL return after the program is confirmed up and SHALL NOT treat Supervisor `RUNNING` as HTTP readiness. When the effective `odoo.conf` has an explicit non-empty `logfile`, that path SHALL be used. When `logfile` is absent or empty, the command SHALL choose `odoo.log` next to the effective `odoo.conf`, create the missing parent directory and file before start, and pass `--logfile {path}` via `resolve_effective_logfile()` in `resources/instance/runtime.py` after the protected-option check (user `--logfile` after `--` remains rejected) WITHOUT editing the user's `odoo.conf`. The same resolver SHALL be used by `odcli logs`, the structured result, and runtime metadata. New isolated environments SHALL write an environment-owned `<environment-root>/odoo.log` into generated `odoo.conf` and create the file with the other artifacts. The same fallback SHALL apply to old/partial isolated configs. Two isolated environments of one project SHALL use different logfiles. `--dry-run` SHALL show the resolved logfile path and Supervisor actions without creating the directory or file. An unwritable fallback SHALL fail before start with a clear message and the exact path. `-d` after the literal `--` SHALL be treated as a native Odoo argument and SHALL NOT be interpreted as the OdCLI option.

#### Scenario: Project run needs no runtime path arguments

- **WHEN** `odcli run` executes from an initialized main checkout whose manifest references valid Python, Odoo entry point, and config
- **THEN** it constructs and launches the foreground program without requiring those paths as CLI arguments

#### Scenario: Project defaults and passthrough compose deterministically

- **WHEN** project context defines default run arguments and the caller supplies allowed arguments after `--`
- **THEN** the captured command contains project defaults followed by the exact caller arguments in their original order

#### Scenario: Project dry-run has no side effects

- **WHEN** `odcli run --dry-run` resolves project context
- **THEN** it emits the bounded execution plan without starting Odoo, mutating the environment catalogue, or writing use metadata

#### Scenario: Environment run retains metadata behavior

- **WHEN** `odcli run` resolves a ready environment and the port preflight succeeds
- **THEN** it records environment use exactly once before executing the captured foreground command

#### Scenario: Native process contract is context-independent

- **WHEN** an environment-based or project-based foreground run is interrupted
- **THEN** the client returns exit `130` and the Supervisor program keeps running

#### Scenario: Native TTY and exit behavior remain unchanged

- **WHEN** `odcli run -- --workers=2` is interrupted with Ctrl-C
- **THEN** the client returns exit `130` and the Supervisor program keeps running

#### Scenario: Port conflict deterministic error

- **WHEN** `odcli run -- --dev=reload` finds the effective bound port occupied by a process that is not this runtime's program
- **THEN** it returns `port-conflict` and performs no launch or use update

#### Scenario: Free port starts Odoo

- **WHEN** `odcli run -- --dev=reload --log-level debug --dev=xml` finds the effective port free
- **THEN** it captures one command with the exact delimiter arguments and starts that program

#### Scenario: Delimiter is required for native arguments

- **WHEN** a caller invokes `odcli run --dev=reload` without the `--` delimiter
- **THEN** Click reports an unknown-option usage error with exit code `2` before SDK resolution or launch

#### Scenario: Bare positional input is rejected

- **WHEN** a caller invokes `odcli run sale` without a literal `--`
- **THEN** the command reports a usage error with exit code `2` and performs no SDK resolution, use update, command construction, or launch

#### Scenario: Protected override is rejected before spawn

- **WHEN** `odcli run -- --database other` or another protected runtime-identity override is invoked
- **THEN** the SDK validator returns a sanitized error before command execution and no child process starts

#### Scenario: Dry-run and execution use one captured argv

- **WHEN** the same allowed delimiter arguments are supplied to dry-run and normal execution
- **THEN** dry-run displays the exact captured program command and normal execution consumes it without reconstructing argv

#### Scenario: Detached flag after literal delimiter is native

- **WHEN** `odcli run -- -d` is invoked
- **THEN** `-d` is passed to Odoo as a native argument and is not interpreted as the detach option

#### Scenario: Detached run provisions an effective logfile

- **WHEN** `odcli run --detach` runs with an `odoo.conf` that has no `logfile`
- **THEN** the command chooses `odoo.log` next to the effective config, creates the parent directory and file before start, passes the path to Odoo without editing the user's config, and uses the same path for `logs`, the structured result, and runtime metadata

#### Scenario: Explicit logfile has priority

- **WHEN** `odcli run --detach` runs with an `odoo.conf` that has a non-empty `logfile`
- **THEN** the explicit path is used and no fallback path is created

#### Scenario: Detached dry-run shows the resolved logfile

- **WHEN** `odcli run --detach --dry-run` runs
- **THEN** the resolved logfile path is shown and no directory or file is created

#### Scenario: Unwritable logfile fallback fails before spawn

- **WHEN** the chosen fallback logfile path cannot be created or opened
- **THEN** the command fails before start with `logfile_unwritable` and the exact path

#### Scenario: Two isolated environments use different logfiles

- **WHEN** two isolated environments of one project run detached
- **THEN** each writes to its own `<environment-root>/odoo.log` and their outputs do not mix

### Requirement: `odcli monitor` command

```bash
odcli monitor [--headless] [--host HOST] [--port PORT] [--no-open] [--detach]
odcli monitor start [--headless] [--host HOST] [--port PORT] [--no-open] [--detach]
odcli monitor stop
odcli monitor restart
odcli monitor status
odcli monitor logs
```

`odcli monitor` and `odcli monitor start` MUST start the FastAPI server as the private Supervisor program `odcli-monitor`, serving `GET /api/v1/snapshot` and `GET /healthz`. Default UI mode serves API plus the React SPA, binds `127.0.0.1`, selects port `8069` then `8100`–`8120` on the first start, and opens a browser after `/healthz` unless `--no-open` or `--headless`. `--detach` returns after readiness without following logs. Without `--detach`, the client follows logs until Ctrl-C, which exits `130` and leaves the server running. The supervised process MUST NOT open a browser. Restart and crash recovery MUST NOT open one either.

`--headless` serves API only. The server accepts only loopback bind addresses. The command requires the dashboard extra; a missing extra exits 1 with the existing `pip install odoo-instance-sdk[dashboard]` hint and does not register a program. `stop`, `restart`, `status`, and `logs` MUST address only the monitor program through Supervisor and MUST work when the HTTP API is down. `stop` of an already stopped monitor succeeds. `restart` restarts only that program and keeps the saved address. `logs` prints a bounded tail without secrets.

#### Scenario: Default UI mode serves SPA and API

- **WHEN** `odcli monitor` runs without `--headless` and the server becomes ready
- **THEN** FastAPI serves `/api/v1/snapshot`, `/healthz` and the React SPA, and the CLI opens a browser once

#### Scenario: Headless serves API only

- **WHEN** `odcli monitor --headless --no-open` runs
- **THEN** `/api/v1/snapshot` and `/healthz` respond, static assets are not mounted, and the browser is not opened

#### Scenario: Missing dashboard extra actionable hint

- **WHEN** `odcli monitor` runs and `fastapi` or `uvicorn` is not installed
- **THEN** it exits 1 with a message containing `pip install odoo-instance-sdk[dashboard]` and registers no program

#### Scenario: Monitor control works while HTTP is down

- **WHEN** the monitor HTTP API is unreachable and the user runs `odcli monitor status` or `odcli monitor stop`
- **THEN** the command talks to Supervisor and does not require a live HTTP response

#### Scenario: Monitor interrupt detaches

- **WHEN** the user presses Ctrl-C while `odcli monitor` is following logs
- **THEN** the client exits `130` and the monitor program keeps running

### Requirement: Stop a selected environment runtime

Top-level `odcli stop` SHALL resolve exactly one initialized project or registered environment through the existing shared context rules. Its help SHALL describe the selected runtime rather than requiring an environment. On Unix it SHALL stop only that owner's Supervisor program when one is registered, after the program command matches the captured launch identity. It SHALL NOT reconstruct that identity from the current checkout. A pre-Supervisor row with no program SHALL keep the existing PID, create-time, argv, cwd, config, and POSIX `pgid == pid` checks before any signal. It SHALL NOT infer ownership from a listening port and SHALL NOT stop other programs.

No matching runtime row, or an already stopped program, SHALL return idempotent success and conditionally clear only that owner's stale row. A mismatched program command SHALL fail closed without stopping it. Rejections SHALL provide bounded sanitized component evidence and SHALL NOT emit raw argument values, passwords, or the full argv. Rich, JSON, and TOON success output SHALL continue to identify `owner_kind`, canonical `project_id`, nullable `environment_id` and `environment_name`, and status consistently. Windows SHALL reject a Supervisor-managed stop with the unsupported message.

#### Scenario: Stop cwd-owned environment runtime

- **WHEN** cwd resolves a running environment whose Supervisor program matches the captured launch identity
- **THEN** `odcli stop` stops only that program, clears only its environment-owned runtime row, and reports environment ownership

#### Scenario: Stop explicitly selected environment runtime

- **WHEN** `odcli --env ENVIRONMENT stop` is invoked outside the worktree with a matching program
- **THEN** it has the same plan, safety, output, and exit behavior as cwd environment resolution

#### Scenario: Stop project runtime after unrelated checkout evolution

- **WHEN** cwd or `--project` resolves an initialized main checkout whose project-owned program still matches its captured launch identity after a branch switch, revision change, or mutable configuration-source change
- **THEN** `odcli stop` stops that program, clears only its project-owned runtime row, reports project ownership with null environment identity, and leaves project registration intact

#### Scenario: Already stopped is idempotent for either owner

- **WHEN** the selected project or environment has no matching runtime row, or its program is already stopped
- **THEN** `stop` succeeds without signaling another process and conditionally clears only the matching stale row when present

#### Scenario: Port occupancy is not ownership

- **WHEN** an unrelated process listens on the selected owner's recorded port
- **THEN** `stop` does not signal it

#### Scenario: Protected binding mismatch is actionable and sanitized

- **WHEN** the program command changes one protected binding from the captured launch identity
- **THEN** `stop` fails without stopping or clearing the row and its error names only the safe differing component, such as `argv: --database`

#### Scenario: Secret-bearing mismatch evidence is bounded

- **WHEN** identity validation encounters a secret-bearing argv position
- **THEN** every output mode emits the common redaction marker where needed and emits neither raw values nor the full command

#### Scenario: Persisted owner or snapshot changes during stop

- **WHEN** the persisted owner kind, owner id, PID, create time, or captured launch identity differs between planning and execution revalidation
- **THEN** `stop` fails closed without stopping or clearing either owner's runtime row

#### Scenario: Pre-migration live row has no snapshot

- **WHEN** `odcli stop` selects a live legacy runtime row without captured launch identity and without a Supervisor program
- **THEN** it reports a sanitized unavailable-identity failure, sends no signal, retains the row, and does not authorize termination from reconstructed current configuration
