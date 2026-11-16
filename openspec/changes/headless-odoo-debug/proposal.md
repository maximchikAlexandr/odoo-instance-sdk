# Proposal

## Why

Agents cannot debug a local Odoo process through a foreground REPL: the session dies with the TTY, and each step has to keep that process alive. GitHub issue #18 needs one non-interactive CLI or SDK call to express one intention and return one bounded result, while the same environment session stays alive underneath.

## What Changes

- Add persistent headless debugging for one ready environment: `odcli debug` verbs `start`, `continue`, `context`, `eval`, `step`, `pause`, `restart`, and `stop`, plus the same operations on a public `DebugResource`.
- Run one private daemon per environment UUID. The daemon owns the Odoo/debugpy process, one reproduction process, breakpoints, bounded output, and a loopback DAP connection. Callers talk to it over a mode-checked Unix socket with versioned newline-delimited JSON.
- Return one immutable `DebugSnapshot` from lifecycle and inspection verbs, and a bounded `DebugValue` from `eval`. Machine output stays inside the existing envelope. `--json` stays removed.
- Preserve `run`, `shell`, monitoring, and the current VS Code profile source. Extend `odcli vscode generate --write` so an SDK-owned profile can be inserted or updated inside existing JSONC without dropping comments, compounds, or unrelated profiles, and without overwriting a manual edit.
- Keep debugpy in the recorded environment interpreter. Do not add it to the SDK package, and do not install it automatically.

## Capabilities

### New Capabilities

- `headless-debug`: persistent one-session debugger for a ready environment, including the public resource, daemon, DAP subset, snapshots, breakpoints, reproduction commands, cleanup, and dry-run plans.

### Modified Capabilities

- `cli-odcli`: register the `debug` command surface and its bounded leaves; allow this one new public resource; record environment use and the HTTP port preflight on debug start; replace the refuse-existing-JSONC rule for `vscode generate` with identity-checked JSONC maintenance. `--json` remains a usage error.

## Impact

- New public models and `DebugResource` in the SDK. CLI callbacks call those methods and render Rich, JSON, or TOON.
- New private daemon, Unix-socket RPC, and debugpy client. Child processes go through `internal/proc`. Plans stay frozen `ProcessStep` / `ActionStep` values.
- Session files live under the existing user data root with mode `0700` / socket `0600`.
- `PUBLIC_LEAF_CASES`, help text, and the README gain the debug verbs and the JSONC update contract.
- `odcli run`, `shell`, logs, monitor, and project-only `vscode generate` printing stay available. Debug itself requires a ready environment and rejects a project-only context.
