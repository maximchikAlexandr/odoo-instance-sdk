## MODIFIED Requirements

### Requirement: Stable machine output

The exact bounded structured leaf inventory is: `init`, `doctor`, `env checkout`, `env list`, `env remove`, `env sync`, `backup list`, `backup show`, `backup validate`, `backup delete`, `db refresh`, `db reset-admin-password`, `db list`, `db restore`, `db drop`, `resource list`, `resource doctor`, `eval`, `exec`, `test`, `module list`, `module update`, `module test`, `translations export`, `deps verify`, `vscode generate`, `db locks`, `db stats`, `db bloat`, `db init-monitoring`, `postgres approve-image`, `postgres status`, `postgres up`, and `postgres stop`. Each SHALL accept command-local `--format rich|json|toon`; `rich` SHALL be the default. Existing `--json` SHALL remain a backward-compatible alias for `--format json`. Supplying `--json` with `--format toon` or `--format rich` SHALL be a Click usage error with exit code `2`; supplying `--json --format json` SHALL be accepted. During normal execution, `run`, interactive `shell`, `psql`, and `logs --follow` SHALL remain raw-streaming and SHALL not emit document output or use a Rich live wrapper. Eligible spawning `run` and `shell` SHALL accept document-format options only together with `--dry-run`; those dry-run paths SHALL suppress native execution and emit one bounded plan document in Rich, JSON, or TOON, with `--json` equivalent to `--format json`. `psql --dry-run` SHALL remain an explicit plan-only exception that emits the shared sanitized native command plan without spawning; normal `psql` remains raw passthrough and SHALL continue to reject `--format` and `--json`.

The CLI SHALL define one CLI-only `OutputMode` with values `rich`, `json`, and `toon`. The mode and envelope types SHALL NOT become public SDK models or FastAPI response models. Each successful or failed bounded operation SHALL first build one JSON-safe CLI envelope v1 containing `schema_version`, `ok`, `command`, `context`, `provenance`, `dry_run`, and `warnings`; success SHALL contain equal `result` and `data`, while failure SHALL omit top-level `result` and `data` and SHALL contain stable `error.code` and sanitized `error.message`. `error` MAY additionally contain an operation-specific JSON-safe `details` field; failures without structured details SHALL omit it and retain their existing v1 shape.

JSON and TOON SHALL serialize that exact envelope without building format-specific result graphs. Decoding a TOON document with the selected strict decoder SHALL yield the same JSON value as decoding JSON output for the same operation. Machine modes SHALL emit exactly one UTF-8 document to stdout with no ANSI, prompt, status, progress, or external log text; diagnostics SHALL go to stderr. Renderer selection SHALL NOT change operation execution, exception mapping, or exit code. Native Click parse failures that occur before output-mode resolution SHALL retain Click's stderr usage output and exit code `2`.

For `env remove`, `backup delete`, `db restore`, and `db drop`, JSON and TOON document modes (including the `--json` alias) SHALL never call `click.confirm`. Without `--yes`, they SHALL NOT execute mutation and SHALL emit exactly one sanitized failure envelope with `error.code="confirmation_required"` and exit code `1`. With `--yes`, JSON and TOON SHALL execute the same operation and normal success/failure mapping. Interactive Rich mode SHALL retain command-specific confirmation behavior. Dry-run SHALL never prompt and SHALL remain non-mutating.

Rich renderers SHALL remain adjacent to the concrete commands whose typed results they render. They MAY use `Table`, `Status`, `Progress`, and `Live` only when appropriate to the operation; they SHALL NOT introduce a generic renderer interface, registry, or DSL. `db stats` and `db bloat` SHALL render separate tables and indexes tables rather than one sparse combined table.

#### Scenario: JSON envelope

- **WHEN** `odcli env list --json` executes
- **THEN** stdout contains exactly one versioned envelope and no progress or log text

#### Scenario: JSON alias preserves envelope v1

- **WHEN** `odcli env list --json` and `odcli env list --format json` run against the same frozen result
- **THEN** each stdout document decodes to the same envelope v1 and contains no ANSI or diagnostic text

#### Scenario: TOON is semantically equal to JSON

- **WHEN** a bounded command succeeds or fails once and its envelope is emitted as JSON and TOON
- **THEN** strict TOON decoding and JSON decoding produce equal Python builtins including `result`/`data`, context, provenance, warnings, and error fields

#### Scenario: Conflicting alias is usage error

- **WHEN** a caller supplies `--json --format toon`
- **THEN** Click exits `2`, does not execute the operation, and does not emit a partial machine document

#### Scenario: Machine diagnostics stay on stderr

- **WHEN** a bounded machine-mode operation reports a sanitized diagnostic in addition to its result
- **THEN** stdout contains one JSON or TOON envelope and the diagnostic is written only to stderr

#### Scenario: Structured failure details preserve the failure variant

- **WHEN** a bounded operation has structured diagnostics required by its capability contract
- **THEN** the machine document has `ok=false`, omits top-level `result` and `data`, retains `error.code` and sanitized `error.message`, and stores those diagnostics only in JSON-safe `error.details`
- **AND** a failure without such diagnostics omits `error.details`

#### Scenario: Machine mutation requires explicit confirmation

- **WHEN** `env remove`, `backup delete`, `db restore`, or `db drop` runs in JSON/TOON mode without `--yes` and without `--dry-run`
- **THEN** no prompt or mutation occurs, stdout contains one `confirmation_required` failure envelope, and the command exits `1`

#### Scenario: Explicit machine mutation executes

- **WHEN** a supported mutating lifecycle leaf is invoked with `--yes` in JSON/TOON mode
- **THEN** the same operation runs once and its result is emitted under the normal renderer-independent exit mapping

#### Scenario: Machine remove requires explicit confirmation

- **WHEN** `odcli env remove ENV --format json`, `--format toon`, or `--json` is invoked without `--yes`
- **THEN** no prompt is rendered, removal is not called, stdout contains one failure envelope with `error.code="confirmation_required"`, and the command exits `1`

#### Scenario: Explicit machine remove executes

- **WHEN** `odcli env remove ENV --yes --format json`, `--format toon`, or `--json` is invoked
- **THEN** the same removal operation runs once and its result is emitted as one document under the normal renderer-independent exit mapping

#### Scenario: Secrets redacted

- **WHEN** an error occurs during checkout or resource lifecycle execution
- **THEN** every machine or Rich error message redacts passwords, config bodies, sensitive environment values, and captured secrets before emission

#### Scenario: Diagnostic machine formats share one result graph

- **WHEN** one frozen diagnostic or resource result is projected as JSON and TOON
- **THEN** both decoded envelopes contain equal typed fields, numeric byte fields, warnings, and completeness state

#### Scenario: Native command dry-run supports every bounded format

- **WHEN** `odcli run --dry-run` or spawning `odcli shell --dry-run` is requested with `--format rich|json|toon` or `--json`
- **THEN** output contains exactly one bounded plan with `dry_run=true` in the selected format
- **AND** `--json` and `--format json` produce equivalent JSON documents
- **AND** no native child stream starts

#### Scenario: Normal native command stays raw

- **WHEN** `odcli run` or interactive `odcli shell` executes without `--dry-run`
- **THEN** its inherited stream is not wrapped in a bounded document or Rich live view

#### Scenario: Normal native command rejects machine options

- **WHEN** `odcli run` or spawning `odcli shell` is invoked with `--format` or `--json` but without `--dry-run`
- **THEN** Click exits `2` before invoking SDK code or starting a process

#### Scenario: Canonical bounded inventory remains single-source

- **WHEN** the stable machine-output characterization gate compares the documented normal-execution leaves
- **THEN** they equal canonical `PUBLIC_LEAF_CASES`, including every backup, database, and resource leaf added by this change
- **AND** no second bounded-leaf table is introduced

#### Scenario: Mutating lifecycle dry-runs are canonical leaves

- **WHEN** the characterization gate exercises `backup delete`, `db restore`, or `db drop` with `--dry-run` in every shared format
- **THEN** each appears exactly once in canonical `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no file, database, configuration, process, or catalogue mutation occurs

#### Scenario: Database drop is a canonical bounded leaf

- **WHEN** the stable machine-output characterization gate exercises `db drop DATABASE --dry-run` in every shared format
- **THEN** `db drop` appears exactly once in canonical `PUBLIC_LEAF_CASES` as `mutating-or-spawning` with required dry-run support
- **AND** no database, session, or catalogue mutation occurs

## ADDED Requirements

### Requirement: Dependency verification uses the selected Python correctly

`deps verify` SHALL run distribution validation through the configured `uv_executable`. For an explicit Python filesystem path it SHALL execute `uv pip check --python <path>` and SHALL NOT ask that interpreter to execute `pip` as a script. For an existing uv Python selector it SHALL use a valid selector-aware uv invocation. The command SHALL succeed only when `pip_check_ok=true` and `missing_imports` is empty, and SHALL expose concrete distribution diagnostics and missing module/import pairs without installing packages.

#### Scenario: Explicit virtual environment path

- **WHEN** project resolution selects an explicit virtual-environment Python path
- **THEN** the captured distribution step starts with the configured uv executable and contains `pip check --python <exact-path>`

#### Scenario: Distribution conflict

- **WHEN** uv pip check exits non-zero and import probes succeed
- **THEN** Rich, JSON, and TOON report failure, include sanitized distribution diagnostics, and the CLI exits 1

#### Scenario: Missing import

- **WHEN** pip check succeeds but a declared external import probe fails
- **THEN** the result identifies the module/import, the envelope has `ok=false`, and the CLI exits 1

#### Scenario: Dependency verification succeeds

- **WHEN** pip check and every import probe succeed
- **THEN** all formats report `pip_check_ok=true`, no missing imports, `ok=true`, and exit 0

### Requirement: Truthful progress for long bounded commands

Normal Rich execution SHALL use the shared observer for `env checkout`, `env sync`, `test`, `module test`, `module update`, `exec`, `eval`, `translations export`, `db refresh --restore`, `db restore`, `postgres up`, and `postgres approve-image`. It SHALL show the current logical step, completed steps, and elapsed time until termination. It SHALL use a spinner/status when duration is unknown and SHALL show percentage only from a trustworthy total. Non-TTY Rich SHALL emit sparse deterministic lines; machine modes and dry-run SHALL emit no runtime progress. Fast read-only commands SHALL remain excluded unless measured evidence classifies them as long.

#### Scenario: Controlled slow step

- **WHEN** a fake long step pauses after its started event
- **THEN** Rich output is observable before completion and later closes the step with elapsed time

#### Scenario: Logical steps are not time percentages

- **WHEN** a command completes two of five planned steps but has no reliable duration total
- **THEN** output may report step counts but does not label the command 40 percent complete

#### Scenario: Progress command fails

- **WHEN** a long bounded command raises or returns failure
- **THEN** the renderer closes, the active step is failed, and existing error/exit semantics remain authoritative

### Requirement: Concise Rich completion line

After a successful bounded command finishes in Rich mode, the CLI SHALL emit exactly one terminal completion line derived from the existing `OutputDocument`. The line SHALL include `status=success` and only applicable fields among `database`, `url`, `backup`, and `modules`; successful `exec` SHALL additionally include `transaction=rollback|commit`. It SHALL not introduce a second result model or command-specific completion formatter. JSON/TOON and native streaming commands SHALL remain unchanged.

#### Scenario: Exec commits successfully

- **WHEN** bounded `exec` succeeds with commit requested
- **THEN** its final Rich completion line contains `status=success transaction=commit` and only applicable summary fields

#### Scenario: Command has no optional summary field

- **WHEN** a bounded command succeeds without database, URL, backup, modules, or transaction data
- **THEN** its final Rich completion line is exactly the common success status without empty placeholders

#### Scenario: Machine output is unchanged

- **WHEN** the same bounded success is emitted as JSON or TOON
- **THEN** stdout remains the one existing envelope and contains no additional completion line

### Requirement: Backup lifecycle CLI

`odcli backup list [--source URL] [--database NAME] [--all] [--limit N] [--cursor CURSOR]`, `backup show <BACKUP_UUID>`, and `backup validate <BACKUP_UUID>` SHALL be read-only bounded leaves usable without running Odoo or requiring a worktree. List SHALL show full UUID, source database/base URL, catalogue time, format, recorded size, state, and actual file presence; show SHALL add sanitized path, checksum, branch, history, and restore/environment relationships. Validate SHALL distinguish an unavailable validator from an invalid archive. `backup delete <BACKUP_UUID> [--dry-run] [--yes]` SHALL use the exact UUID deletion command and its confirmation contract.

#### Scenario: Default backup list

- **WHEN** `backup list` runs without `--all`
- **THEN** it deterministically returns only available catalogue records and separately reports actual file presence

#### Scenario: Backup details

- **WHEN** `backup show` receives a known UUID
- **THEN** every format represents the same record, audit history, and known relationships without starting Odoo

#### Scenario: Validator is unavailable

- **WHEN** dump validation requires `pg_restore` and it is not available
- **THEN** the command reports validation unavailable rather than labelling the archive corrupt

#### Scenario: Backup deletion preview

- **WHEN** `backup delete UUID --dry-run` executes
- **THEN** the plan identifies the exact file, recorded size, state, and relationships without prompting or mutation

### Requirement: Database inventory and registered restore CLI

`odcli db list [--tracked]` SHALL project the read-only project-cluster inventory in all bounded formats. `odcli db restore <BACKUP_UUID> [--target DATABASE] [--reset-admin-password] [--dry-run] [--yes]` SHALL invoke the registered-local-backup preparation path; normal Rich execution SHALL confirm unless `--yes`, machine execution SHALL require `--yes`, and dry-run SHALL not prompt. A successful restore SHALL switch the project default only after all restore postconditions and optional administrator reset succeed.

#### Scenario: Database list is read-only

- **WHEN** `db list` encounters a PostgreSQL database absent from catalogue provenance
- **THEN** it reports unknown origin and writes no dropped audit event

#### Scenario: Tracked list

- **WHEN** `db list --tracked` runs
- **THEN** it returns only exact cluster/database identities having proven restore or lifecycle relationships

#### Scenario: Restore default target

- **WHEN** `db restore UUID --yes` omits `--target`
- **THEN** it restores to a generated collision-free name, preserves the backup and prior database, and switches default after full success

#### Scenario: Restore preflight fails

- **WHEN** UUID, file, checksum, format, cluster binding, or target-name preflight fails
- **THEN** the command exits 1 with no database or project-config mutation

### Requirement: Resource inspection CLI

`odcli resource list` and `odcli resource doctor` SHALL be bounded read-only leaves available from initialized project context and SHALL project the `local-resource-lifecycle` results through Rich, JSON, and TOON. They SHALL never prompt, delete, repair, register, or reconcile lifecycle state.

#### Scenario: Resource list machine parity

- **WHEN** one frozen resource inventory is emitted as JSON and TOON
- **THEN** decoded envelopes are equal and distinguish logical bytes, measured bytes, completeness, ownership, active use, and reclaimability

#### Scenario: Doctor finds leftovers

- **WHEN** doctor detects missing catalogue files, owned `.part` leftovers, unknown owned-directory files, filestore uncertainty, or cleanup-failed environments
- **THEN** Rich output gives sanitized actionable findings and machine output contains the same typed findings without changing them

### Requirement: CLI init tests use an isolated catalogue

All tests that invoke `odcli init` SHALL replace the `get_catalog_path()` symbol used by the CLI and use controlled worker-local XDG/data roots and sentinel catalogue paths through one shared fixture. Parallel full-suite execution SHALL not resolve, open, read, write, or modify the production-resolved user catalogue path, and monitor tests SHALL not observe projects registered by another test worker.

#### Scenario: Parallel init suite preserves user data

- **WHEN** the full init-related suite runs in parallel under a spy that rejects access to the production-resolved catalogue path
- **THEN** every catalogue open/read/write targets a controlled worker-local sentinel path and the production path is never opened or snapshotted

#### Scenario: Worker catalogues are isolated

- **WHEN** two test workers initialize different temporary projects
- **THEN** each worker's monitor fixtures see only their own temporary catalogue records
