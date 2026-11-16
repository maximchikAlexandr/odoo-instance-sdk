# Design

## Context

See proposal.md for why this exists. The CLI already resolves a ready environment or a project through `ready_instance`, launches Odoo from a frozen `Command` in `OdooInstance.run_foreground_command`, and renders bounded leaves through `run_or_preview`. `odcli vscode generate --write` still refuses any existing `launch.json`. `--json` is already a usage error in `cli-odcli`. Child processes must enter through `internal/proc`. Public plans are frozen `ProcessStep` and `ActionStep` values.

## Goals / Non-Goals

**Goals:**

- One public `DebugResource` owns session semantics. The CLI only parses, calls a `*_command()` sibling, and renders.
- One daemon per environment UUID owns Odoo, the reproduction process, breakpoints, buffers, and the DAP socket.
- Dry-run and execution share one captured plan. Dry-run does not connect or spawn.
- VS Code maintenance edits one owned profile inside existing JSONC text.

**Non-Goals:**

- A DAP framework, a second debugger backend, gRPC, or a public daemon command.
- Installing debugpy into the SDK environment.
- Restoring `--json`.
- Persisting breakpoints after `stop`, multi-worker debugging, or remote attach.

## Decisions

### Public resource, private daemon

`DebugResource` lives next to the other resources and is reached from the client the same way as `OdooInstance`. Each mutating or spawning method has a `*_command()` sibling. The convenience method calls that sibling once and returns `.run()`.

The daemon is a private module started as a long-running process step: the recorded interpreter runs `python -m odoo_instance_sdk.internal.debug.serve` with the environment id. The CLI never builds that argv itself. Subsequent verbs are action steps that perform one Unix-socket RPC. They do not spawn.

Alternative considered: keep the DAP client inside each CLI process. That cannot preserve a stopped session across processes, which is the point of the issue.

### Plans name real effects

`start_command` captures, in order: dependency and bootstrap actions already used by foreground run, an action for the artifact lock and secret config, a process step for the debugpy launch, and an action for registry plus environment use. The debugpy argv is `python -m debugpy --listen 127.0.0.1:0 --wait-for-client <odoo-bin> <existing args>`. Port 0 keeps the plan stable and loopback-only. The daemon reads the assigned port from debugpy's private startup line and never returns it.

In-process effects (session directory, socket permissions, breakpoint commit, cleanup) are `ActionStep` values. They are not shell strings.

`continue -- COMMAND` captures the reproduction argv as a process step on the daemon side. The client plan records the action "send continue RPC" plus the argv that the daemon will exec. Execution sends that captured argv. It does not rebuild it from later CLI state.

### Session files

State lives at `<user-data>/debug/<environment-id>/` with mode `0700`. The directory is created with `O_EXCL` semantics so two starts cannot both win. Contents are a metadata file, a `0600` socket, and the daemon pid with its start time. A live check requires the pid to exist and its start time to match. A dead pid or a reused pid is stale and is cleaned before a new start. RPC messages are one JSON object per line and include a protocol version. A mismatch returns `protocol-mismatch` and changes nothing.

### DAP subset

One private client speaks debugpy's `Content-Length` framing. It handles the requests and events named in the issue and nothing else. Handshake order is fixed by the integration test, not by a public adapter interface. The controller translates DAP stops into the snapshot model and drops thread, frame, and variable references before anything is serialized to a caller.

### Breakpoints and limits

The controller keeps one map of `(absolute path, line)` to an optional condition, plus at most one temporary `--to` entry. A command validates every path and line, then replaces the affected files' DAP set in one update. Snapshot limits are the numbers in the headless-debug spec. Output uses an in-memory ring capped at 64 KiB. Redaction reuses the existing secret projection on names and string values before the snapshot is frozen.

### Reproduction and signals

The trigger is `spawn` with `shell=False`, the runtime cwd, and a new session. Its process group is the only group `stop` and `restart` terminate besides the Odoo group. The client treats `SIGINT` as "close my socket". The daemon treats that disconnect as the end of the wait, not as a stop. Exit 130 is produced by the CLI.

### VS Code JSONC

A narrow scanner finds the `configurations` array and the object whose preceding comment contains the SDK identity for that project or environment. A second comment stores a hash of the last generated profile fields. The editor splices only that object, or inserts one, and copies every other byte through. Invalid or ambiguous structure fails before any write. Apply re-reads the file and refuses when the bytes differ from the planned source. This stays on the existing CLI-only `vscode generate` leaf because it writes an editor file rather than an Odoo runtime.

### Output and errors

Debug failures use one `DebugError` with a stable code. The CLI maps it into envelope `error.code`, `error.message`, and `error.details` containing `hint`, `state`, and `snapshot` when the spec requires them. Rich rendering is a local table in the debug command module. There is no shared renderer hierarchy.

### Tests

Unit tests cover parsing, plans, snapshot limits, redaction, breakpoint atomicity, RPC framing, and cleanup with a fake process executor. One serial integration test uses real debugpy and a fake `odoo-bin`, driven by separate `odcli` subprocesses. Real Odoo is not a CI dependency. `PUBLIC_LEAF_CASES` is the only new leaf inventory.

## Risks / Trade-offs

- [debugpy port 0 startup line changes] → the integration test pins the parse, and failure becomes `protocol-failure` without leaving the child running.
- [A hung reproduction command outlives a crashed daemon] → crash cleanup records both process groups and reaps them on the next start's stale reconciliation as well as in the daemon's exit handler.
- [JSONC splice corrupts comments] → the editor is text-splice only, and tests cover comments, compounds, and a same-name foreign profile.
- [Eval runs arbitrary code] → documented, local, and rejected unless the target is stopped. It is not exposed on a network socket.

## Migration Plan

No catalog migration. Existing `launch.json` files without an SDK identity comment are left intact and gain one inserted profile on the next `--write`. Rollback is removing the debug command and the session directory. Old sessions are ignored once the daemon code is gone because nothing else reads that directory.

## Open Questions

None. The startup timeout, wait cap, snapshot limits, and the decision to keep `--json` removed are specified.
