# Proposal

## Why

Agents can tail an Odoo logfile, but they cannot select one run, filter typed events, or resume after a named boundary without a log server. GitHub #17 asks for that opt-in contract on the existing `odcli logs` command and public SDK. PostgreSQL `jsonlog` stays a later stage of the same issue and is not started here.

## What Changes

- Add an optional `[logging.odoo]` section in `.odcli/project.toml`. Absence keeps today's raw `odcli logs` and Odoo run logfile behavior.
- When `structured = true`, each new `odcli run` gets one opaque `run_id`, a private environment-owned logfile, and non-secret `meta.json`. The launch plan shows that logfile and the exact Odoo argv and does not create the run directory.
- Parse the Odoo 19 text logfile into one immutable `LogEvent`. Continuation lines stay with the previous event. Unparsed lines are kept with `parsed = false`.
- Add `odcli logs search`, `odcli logs cursor`, and `odcli logs mark` on the existing logs command, which becomes a Click group whose bare invocation still tails raw text.
- Add opaque, rotation-aware cursors and environment-scoped marks. Bounded Rich, JSON, and TOON share one page envelope. JSONL is the streaming and export form, including follow checkpoints.
- Expose the same query through `OdooInstance.iter_log_events()` and public cursor and mark operations. Do not add a log store, collector, or second event model.
- Show the effective Odoo logging settings in the existing `odcli doctor --format json` result.
- Count owned run logs as environment files and delete only those owned run directories during environment removal.

## Capabilities

### New Capabilities

- `structured-odoo-logs`: Odoo 19 logfile parsing, `LogEvent`, run selection, cursors, marks, and the shared search page.

### Modified Capabilities

- `project-init`: Optional non-secret `[logging.odoo]` manifest section and its validation.
- `cli-odcli`: `odcli logs` group compatibility, search/cursor/mark leaves, doctor logging facts, and the JSONL exception on search.
- `server-lifecycle`: Structured run logfile override through the existing run command plan, plus `iter_log_events()` beside unchanged `iter_logs()`.
- `development-environment`: Owned per-run log directories, their inclusion in environment file accounting, and their removal with the environment.

## Impact

Stage A touches project manifest parsing, doctor JSON, the Odoo run argv snapshot, environment artifact cleanup, the logs Click tree, the public instance log API, and the single CLI leaf inventory. Raw logs, `--tail`, and `--follow` stay available when structured logging is off. No new runtime service, index, database table, or dependency is added. Stage B (managed PostgreSQL `jsonlog`, cluster cursors, and multi-source marks) remains specified by GitHub #17 and is outside this change. The removed global `--json` flag stays removed; structured search uses `--format json`.
