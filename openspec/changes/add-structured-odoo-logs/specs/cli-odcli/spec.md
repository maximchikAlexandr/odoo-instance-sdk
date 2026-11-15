# Spec Delta

## MODIFIED Requirements

### Requirement: `odcli logs`

```bash
odcli logs
odcli logs --tail 50
odcli logs --follow
odcli --project PATH --env SELECTOR logs --follow
```

`odcli logs` MUST remain the raw-text reader and MUST become a Click group whose bare invocation still accepts the existing tail and follow options.

Without `[logging.odoo]`, the bare command MUST:

- resolve the ready environment through the same `ready_instance` path as `run` and `shell`;
- call `instance.iter_logs(tail=N, follow=F)` and write raw log text to stdout;
- accept `-n, --tail INTEGER` (default 100, MUST be `>= 1`) and `-f, --follow`;
- send diagnostics to stderr and use a non-zero failure status;
- exit 130 when follow is interrupted by Ctrl+C;
- avoid asking for config, database, worktree, or Python paths again;
- avoid `record_use`, port preflight, and `postgres up`;
- avoid creating or changing the logfile;
- reject `--grep`, `--errors`, `--since`, and a JSON snapshot on the bare command.

With structured Odoo logging enabled, the bare command MUST keep that raw-text output and those limits, but MUST read `current` when an active run exists and otherwise `last`. It MUST NOT accept an arbitrary filesystem path. Filtering on the bare command remains shell composition: `odcli logs | rg ERROR`.

#### Scenario: Logs inside worktree
- **WHEN** `odcli logs` runs inside a registered worktree with a readable configured logfile and structured logging is off
- **THEN** the last 100 lines of that file are printed on stdout

#### Scenario: Logs follow
- **WHEN** `odcli logs --follow` runs
- **THEN** the CLI streams appended lines until interrupted and exits 130 on Ctrl+C

#### Scenario: Logs missing logfile
- **WHEN** the bound logfile is absent, empty, missing, or unreadable and structured logging is off
- **THEN** the command returns a non-zero error with the resolved path and reason and creates no file

#### Scenario: Invalid tail
- **WHEN** `odcli logs --tail 0` runs
- **THEN** the command returns a deterministic non-zero error

#### Scenario: Structured bare logs select a run
- **WHEN** structured logging is on and `odcli logs` is run without a subcommand
- **THEN** raw text comes from the active run, or from the latest run when none is active, and no JSON snapshot is written

### Requirement: Single format selector and typed field projection

Format-aware commands SHALL expose only `--format rich|json|toon`, default to Rich, and reject removed `--json` as an ordinary Click usage error, except `odcli logs search`, which SHALL also accept `--format jsonl`. Eligible bounded reads (`env list/show`, `backup list/show`, `resource list/doctor`, `db list/locks/stats/bloat`, `module list`, and `ps`) SHALL accept comma-separated documented dotted `--fields` only with explicit JSON or TOON, recursively derive every allowed root and nested path from that leaf's concrete typed result schema without a command/field registry, project only successful `result`/`data`, preserve repeated-row order and all envelope/structural metadata, and fail before execution for unknown or ineligible fields. [Source: GH#62]

#### Scenario: Project repeated fields
- **WHEN** an eligible bounded read uses valid dotted fields in JSON and TOON
- **THEN** decoded documents are equal, every repeated row retains order with only selected data, and envelope, pagination, completeness, capabilities, timestamps, warnings, context, provenance, dry-run, and errors remain unfiltered

#### Scenario: Reject invalid projection
- **WHEN** `--fields` is used with Rich, unsupported output, an unknown path, mutation, error, dry-run plan, native passthrough, interactive, or streaming command
- **THEN** Click exits `2` before the operation runs

#### Scenario: Typed schema is the field authority
- **WHEN** an eligible leaf's concrete typed result adds or removes a nested field
- **THEN** accepted dotted paths change from that schema alone, including paths through repeated structures, without updating any projection allowlist or command-name field table

#### Scenario: Logs search accepts JSONL
- **WHEN** `odcli logs search --format jsonl` runs
- **THEN** stdout contains one JSON object per line and the removed `--json` flag is still rejected

## ADDED Requirements

### Requirement: Structured log commands
`odcli logs` SHALL expose `search`, `cursor`, and a nested `mark` group with `create`, `show`, `list`, and `delete`. Search, cursor, and mark reads SHALL use the public instance log operations and SHALL NOT gain a dry-run planner. Mark create, replace, and delete SHALL use one inspectable command whose dry-run changes nothing. Bounded Rich, JSON, and TOON search results SHALL use the shared envelope. The bare logs command and `logs search --follow` SHALL remain streaming exceptions. No separate `errors`, `summary`, `show`, or `around` log command SHALL be added.

#### Scenario: Search uses the shared page
- **WHEN** `odcli logs search --format json` returns a bounded page
- **THEN** the result contains `events`, `next_cursor`, `has_more`, `window_end_cursor`, and `complete`

#### Scenario: Mark dry-run writes nothing
- **WHEN** `odcli logs mark create before-tests --dry-run` runs
- **THEN** the plan is shown and no mark file is created

### Requirement: Doctor reports effective Odoo logging
`odcli doctor` structured output SHALL include the effective Odoo logging settings `structured`, `level`, and `handlers`. An absent `[logging.odoo]` section SHALL be reported as structured logging disabled without failing the doctor run for that reason. Doctor SHALL NOT gain a mutable logging configuration command.

#### Scenario: Absent logging section
- **WHEN** `odcli doctor --format json` runs for a project without `[logging.odoo]`
- **THEN** the result reports structured Odoo logging as disabled and the command's success still depends only on the existing checks

#### Scenario: Configured logging section
- **WHEN** the manifest enables structured logging with a level and handlers
- **THEN** the doctor JSON result reports those effective values
