# Design

## Context

Raw Odoo logs already flow through `OdooInstance.iter_logs()` and the `odcli logs` Click command. Detached and foreground launches already build one `ExecutionPlan` and inject `--logfile` from `resolve_effective_logfile()`. Generated environment config keeps an environment-owned `odoo.log`. Doctor JSON is the existing structured project report. Bounded CLI output goes through one envelope, and `PUBLIC_LEAF_CASES` is the only CLI leaf inventory. See `proposal.md` for why this change exists.

## Goals / Non-Goals

**Goals:**

- Keep one command snapshot for a structured run, including the run id, logfile argument, and the in-process directory action.
- Parse and query the Odoo 19 text logfile in process. Persist only the raw logfile, private run metadata, a generation index, and mark files.
- Make cursors self-contained and identity-bound without publishing paths or byte offsets.
- Reuse the public iterator from the CLI. Search and cursor stay planner-free. Mark create, replace, and delete use an action-step command.

**Non-Goals:**

- PostgreSQL `jsonlog`, cluster log discovery, and multi-source marks.
- A collector, index, database table, or new dependency.
- Restoring the removed global `--json` flag.
- Retention, compression, or automatic marks around module and test commands.

## Decisions

### 1. Opt-in manifest section

Add a frozen `OdooLoggingConfig` on `ProjectConfig` for `[logging.odoo]`. `structured = false` and an absent section both leave the current run and raw-log paths unchanged. `level` and `handlers` are applied only to a new structured run by appending the existing Odoo `--log-level` and repeatable `--log-handler` arguments inside the captured argv. The generated `odoo.conf` is not rewritten and no second config file is created.

Alternative: store the choice on the environment row. Rejected because the issue puts the switch in the project manifest and doctor already reads that manifest.

### 2. Run directory beside the environment logfile

A structured run id is `run_` plus a UUID hex, chosen while the command is built. The private directory is `<environment-root>/logs/<run_id>/` with mode `0700`. Execution creates `odoo.log` and `meta.json` at mode `0600` as one action step before spawn. Dry-run renders that path and does not create it. The process receives one `--logfile` override. Foreground and detached launches share the helper that adds those steps to the existing command; they do not grow a second argv builder.

`meta.json` stores run id, environment id, branch, commit, start, end, exit code, pid, process create time, and state. Git identity is captured with the launch plan when a worktree exists. A git read failure stores nulls and does not block launch. Launch writes `running`. A later stop or read reconciles `finished`, `interrupted`, or `stale` from the existing runtime identity. No watcher is added.

Alternative: put the run id in the catalog. Rejected because the issue forbids a new table and the logfile remains the canonical store.

### 3. One parser and one iterator

The Odoo 19 prefix is `YYYY-mm-dd HH:MM:SS,mmm PID LEVEL DB LOGGER: message`. Naive timestamps are interpreted in the host local zone and emitted as UTC RFC 3339. Continuation lines append to the open event. The public method is `OdooInstance.iter_log_events(...)`. Cursor and mark helpers on the same instance return the models the CLI serializes. `iter_logs()` stays the raw reader and is what the bare command uses when structured logging is off. When it is on, the bare command still prints raw text, but from the selected run file through a selector that accepts only `current`, `last`, or a run id.

### 4. Cursor token and generations

The token is unpadded URL-safe base64 of a versioned JSON object: version, source, project id, environment id, run id, generation id, record boundary, and a checksum. The checksum makes corruption `cursor_invalid`. An identity mismatch is `cursor_wrong_instance`. The generation index lives in the run directory and maps a generation id to device and inode. The first observation of `odoo.log` is generation 1. After a rename to `odoo.log.N`, the same inode keeps that generation id. A new inode becomes the next generation. A missing generation is `cursor_expired` and includes the earliest retained cursor when one exists. A retained inode whose size or leading window no longer matches the cursor boundary is `source_reset`. Errors use the stable codes and omit paths.

The scan walks generations in order and yields positions between records. Filters do not move the position backward. The first bounded search freezes the high-water cursor. `--until-cursor` stops before records past that boundary.

Alternative: encode the inode in the token. Rejected because a restart must resolve the generation from the retained index, and the token must stay free of filesystem identity.

### 5. Marks

Marks live in `<environment-root>/logs/marks/<name>.json`, mode `0600`, written with `os.replace`. Names match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Create reads to the current high-water boundary first, then writes the cursor. Create, replace, and delete return a `Command` whose only mutation is an action step, so dry-run performs no write. Show and list are read-only and have no planner.

### 6. CLI leaves

`logs` becomes a group with `invoke_without_command=True`. The leaf walker treats that callback as the existing `("logs",)` leaf so the raw command stays in `PUBLIC_LEAF_CASES`. New leaves are `logs search`, `logs cursor`, and `logs mark create|show|list|delete`. Search without follow is bounded and uses the shared envelope for Rich, JSON, and TOON. `jsonl` is local to search: one object per line, and follow emits `{"type":"checkpoint","cursor":...}` at least every 20 events. Follow interrupted by Ctrl+C exits 130. Search does not accept `--dry-run`. Mark mutations do. The removed `--json` flag stays rejected.

Doctor adds a read-only `logging` check whose facts are `structured`, `level`, and `handlers`. An absent section is `structured: false` and does not by itself fail doctor.

### 7. Cleanup and accounting

Environment removal deletes `<environment-root>/logs` only as part of the existing owned-artifact cleanup after the current provenance checks. The directory walk that fills `other_files_bytes` includes that tree. No new retention policy is introduced.

## Risks / Trade-offs

- [Host-local Odoo timestamps have no zone] → Convert with the host local zone at read time and document that a machine zone change can shift displayed instants. Order still comes from generation and position.
- [External logrotate delay can leave a generation unindexed] → Index on read as well as on launch, and fail closed with `cursor_expired` or `source_reset` instead of guessing.
- [A large logfile makes a linear scan slow] → Accept the issue's limit: no index until a measured problem exists. Bounded pages stop at `--limit` and the frozen window.
- [Foreground exit and detached stop can disagree about the final state] → Only the SDK writes `meta.json`, and a read reconciles a dead pid to `stale` when no exit was recorded.

## Migration Plan

Existing projects keep current behavior until `[logging.odoo]` sets `structured = true`. Enabling it affects new runs only. Rollback is removing the section. Already created run directories are ordinary environment files and disappear with environment removal. No catalog migration is required.

## Open Questions

None. Stage B remains a later change and does not alter these tasks.
