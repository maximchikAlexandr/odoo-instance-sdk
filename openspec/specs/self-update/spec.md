# self-update Specification

## Purpose
Define a provenance-aware, recoverable self-upgrade flow for OdCLI uv-tool installations, including immutable revision resolution, migration, verification, and rollback contracts.

## Requirements

### Requirement: `odcli update` supported scope

`odcli update` SHALL support only OdCLI installed as a `uv tool` from `maximchikAlexandr/odoo-instance-sdk`. The SDK SHALL expose `update_command()` returning `Command[UpdateResult]`; `PUBLIC_LEAF_CASES` SHALL set `sdk_primitive=update_command` (`cli_only_reason` forbidden). Expression SHALL NOT appear in this flow. Default `--ref` SHALL be `main`. `--dry-run` SHALL emit the frozen redacted plan and SHALL NOT launch a process, prompt, or mutate. CLI convenience SHALL delegate and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Unsupported provenance SHALL return `unsupported_install`. Source SHALL come from PEP 610 / uv-tool metadata, not cwd or the Odoo project remote.

Inspect SHALL be in-process ActionSteps. `PUBLIC_LEAF_CASES` SHALL contain two rows for the same Click path: `update` (`mutating-or-spawning`) and `update --check` (`process-previewable-read-only`), both `sdk_primitive=update_command`. When `--ref` is a 40-character lowercase hex SHA equal to installed `vcs_info.commit_id`, mutating `update` SHALL return `already_current` with zero ProcessSteps and SHALL NOT reinstall. Otherwise the mutating Command SHALL contain exactly two `internal/proc` ProcessSteps with argv known at plan time: `("uv", "tool", "install", "--force", "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<ref>")` then `(<uv-tool-odcli-absolute-path>, "update", "--format", "json")` with env `ODCLI_MAINTENANCE=1` only. Maintenance SHALL run Alembic/`storage_migration.py` then verify and write one JSON envelope v1 `UpdateResult` on stdout; the parent SHALL deserialize that document. Maintenance SHALL NOT call uv and SHALL NOT re-enter install. Parent `.run()` SHALL wait for those two steps and SHALL NOT spawn a third process. There SHALL NOT be a second Click command for maintenance.

#### Scenario: unsupported install is refused

- **WHEN** `odcli update` runs on a pipx/system/editable/unknown install
- **THEN** it returns `unsupported_install` with `manual_argv` (`tuple[str, ...] | None`) if known and changes nothing

#### Scenario: source comes from installed provenance

- **WHEN** `odcli update` runs on a supported uv-tool install
- **THEN** the source is determined from `direct_url.json`/uv tool metadata, not from cwd or the Odoo project Git remote

### Requirement: `odcli update` nine-phase flow

`odcli update` SHALL run nine phases: Inspect, Resolve, Preflight, Quiesce, Snapshot, Install, Migrate-in-a-new-process, Verify, Commit/Cleanup. It SHALL be a coordinating process and SHALL NOT continue running old Python code after replacing its own environment.

1. **Inspect** — in-process `read_uv_tool_direct_url()` plus `sys.executable` and uv-tool layout; check `uv` presence, write-access, and supported provenance.
2. **Resolve** — mutating `--dry-run` SHALL NOT spawn. For a mutable branch or tag, `--check` SHALL run one read-only ProcessStep `("git", "ls-remote", "--exit-code", "https://github.com/maximchikAlexandr/odoo-instance-sdk.git", "refs/heads/<ref>", "refs/tags/<ref>", "refs/tags/<ref>^{}")`; an annotated tag SHALL resolve to its peeled commit. A non-zero exit or output without a full commit SHA SHALL return `unsupported_install` with a concrete diagnostic and manual install argv. A 40-character lowercase hex `--ref` is already immutable and SHALL require no resolver process. Mutating `already_current` without uv happens only when that SHA equals installed `vcs_info.commit_id`.
3. **Preflight** — check Python/platform compatibility, `shutil.disk_usage(get_user_root()).free` minus `max(1 GiB, 10%)`, user-data schema, and the full migration path to target before any mutation. Shortfall is `preflight_failed`. Preflight SHALL NOT spawn uv.
4. **Quiesce** — acquire `exclusive_lock` on `get_locks_dir()/odcli-update.lock`. On conflict, return the existing lock error with that path. The Odoo runtime SHALL NOT be stopped unless a migration explicitly requires it.
5. **Snapshot** — save a verifiable rollback snapshot of affected metadata: current exact install requirement/ref, package revision, SQLite catalog, and other files actually touched by the migration plan. Large backup/filestore data SHALL NOT be copied without need.
6. **Install** — run ProcessStep 1 through `internal/proc` with `shell=False`. When `--ref` is a 40-char SHA this is an exact SHA install; when it is `main` or a tag, uv resolves inside this one install (no second argv rebuild).
7. **Migrate in a new process** — run ProcessStep 2 `(odcli, "update", "--format", "json")` with `ODCLI_MAINTENANCE=1`. The old interpreter SHALL NOT import new modules over itself. Journal `get_user_root()/update/journal.json` with frozen fields `phase`, `target_ref`, `snapshot_sha`, `maintenance_pid`. Snapshot `get_user_root()/update/snapshot/`. Verify lives inside this process.
8. **Verify** — performed by ProcessStep 2, not a third parent spawn.
9. **Commit/Cleanup** — mark success only after verify; delete the snapshot directory.

`--dry-run` SHALL NOT launch a process. For a mutable branch or tag, `--check` SHALL be the only Resolve spawn; an exact SHA requires none. Passing `--check` and `--dry-run` together SHALL be a Click usage error with exit code 2 before SDK resolution.

#### Scenario: already current is a no-op for an exact SHA

- **WHEN** `--ref` is a 40-character lowercase hex SHA equal to installed `vcs_info.commit_id`
- **THEN** `already_current` is returned with zero ProcessSteps and no migrations or reinstall run

#### Scenario: migrations run in a new process

- **WHEN** the install phase completes
- **THEN** migrations are executed by a new subprocess OdCLI, not by the old loaded interpreter

#### Scenario: dry-run shows the plan

- **WHEN** `odcli update --dry-run` runs
- **THEN** the install + migration plan is shown and the uv tool, catalog, journal, and project files are unchanged

### Requirement: Coordinator over existing Alembic and storage migrations

A coordinator SHALL call existing Alembic revisions and `internal/storage_migration.py` in order. A parallel competing version scheme or extra from/to registry type SHALL NOT be created. Update-specific calls SHALL NOT be scattered across CLI commands. Expression SHALL NOT be used for lock, rollback, compensation, or lifecycle.

Migrations SHALL run forward only and strictly in order. A missing step, an unknown newer schema, or an absent migration path SHALL block the update before data changes. Each step SHALL be transactional where possible and resumably safe after interruption; multi-step file operations SHALL use existing lock/journal/verification patterns. Repository-local `.odcli` of another project SHALL NOT be migrated without explicit need; if project-local migration is required, the plan SHALL enumerate the projects and SHALL NOT scan all of home unboundedly.

The package update SHALL be considered incomplete until ProcessStep 2 has finished migrations and verify. With an unfinished journal, `odcli update` SHALL resume; every other command SHALL fail with `update_incomplete` and SHALL NOT resume. Commands SHALL NOT run on a partially migrated state.

#### Scenario: missing migration path blocks update

- **WHEN** the target schema is unknown or no migration path exists
- **THEN** the update is blocked before data changes

#### Scenario: resumable after interruption

- **WHEN** a migration step is interrupted
- **THEN** re-running the coordinator resumes the step safely via its idempotency/resume policy

#### Scenario: normal commands refuse partially migrated state

- **WHEN** a normal command other than `odcli update` runs with an unfinished migration journal
- **THEN** it fails with `update_incomplete` and does not resume

#### Scenario: update resumes unfinished journal

- **WHEN** `odcli update` runs with an unfinished migration journal
- **THEN** it resumes through the coordinator

### Requirement: `odcli update` rollback and unknown outcome

Before install, any error SHALL leave the system unchanged. After install: a failure before the first irreversible migration SHALL restore the previous exact uv-tool revision and snapshot metadata and verify the old version starts. A migration step SHALL NOT declare itself rollback-safe without an implemented and tested reverse action or snapshot restore. If safe automatic rollback is impossible, the command SHALL preserve journal and snapshot and return `update_incomplete` with a frozen recovery `ProcessStep` whose argv is `("uv", "tool", "install", "--force", "odoo-instance-sdk @ git+https://github.com/maximchikAlexandr/odoo-instance-sdk.git@<snapshot-sha>")` through `internal/proc`, `shell=False`. That recovery SHALL NOT be an `ActionStep` and SHALL NOT be an invented shell string.

A timeout or network loss during `uv` SHALL NOT automatically mean the install failed: the command SHALL first re-determine the actual installed revision. A downgrade SHALL NOT be performed without an explicit `--allow-downgrade`; downgrade SHALL only be allowed with a proven reverse migration path or a compatible snapshot restore. `sudo`, shell-profile changes, and deletion of previous data/rollback snapshot before successful verify SHALL NOT be used.

#### Scenario: install failure leaves old version working

- **WHEN** the install phase fails
- **THEN** the previous working version is unchanged and starts normally

#### Scenario: migration failure rolls back or reports incomplete

- **WHEN** a migration step fails
- **THEN** either an automatic rollback restores and verifies the old version, or the command returns `update_incomplete` with the snapshot, journal, and the frozen recovery `ProcessStep` uv argv

#### Scenario: timeout re-checks revision

- **WHEN** `uv` times out after a potential install
- **THEN** the command re-determines the actual installed revision before any re-install

### Requirement: `odcli update` UX and typed result

`odcli update` SHALL support Rich, JSON, and TOON with one typed contract. The result SHALL contain at least: `outcome` (`updated`, `already_current`, `unsupported_install`, `preflight_failed`, `rolled_back`, `update_incomplete`); source repository; previous/target/final version and full commit SHA; executable/tool-environment path in a safe normalized form; executed/skipped migration IDs and final schema versions; snapshot/journal state; rollback outcome; one concrete next step; and per-phase duration without secrets and without GitHub credentials/tokens.

Interactive confirmation SHALL be required before changing the install and data. `--yes` SHALL permit non-interactive execution after a successful preflight. `--no-input` without `--yes` SHALL exit before mutations. `odcli update --check` SHALL be the read-only variant with the `git ls-remote` ProcessStep defined by Resolve for mutable refs and no resolver process for an exact SHA. `--dry-run` SHALL be a full plan without spawning. No phase SHALL log GitHub credentials, environment secrets, project passwords, or private config contents.

#### Scenario: check is read-only

- **WHEN** `odcli update --check` runs
- **THEN** installed and target version/full SHA are shown and nothing changes

#### Scenario: typed result carries phases and migration IDs

- **WHEN** an update completes
- **THEN** the result contains outcome, source, previous/target/final version and SHA, executable path, executed/skipped migration IDs, final schema versions, snapshot/journal state, rollback outcome, one next step, and per-phase duration without secrets

#### Scenario: non-interactive without --yes exits before mutations

- **WHEN** `odcli update --no-input` runs without `--yes`
- **THEN** the command exits before any install or data mutation

#### Scenario: downgrade requires allow-downgrade and a proven reverse path

- **WHEN** `odcli update --ref <older> --allow-downgrade` runs with a proven reverse migration path or compatible snapshot restore
- **THEN** the downgrade is allowed; without `--allow-downgrade` it is refused

#### Scenario: no secrets in any phase

- **WHEN** any phase of `odcli update` runs
- **THEN** no GitHub credentials, environment secrets, project passwords, or private config contents are logged
