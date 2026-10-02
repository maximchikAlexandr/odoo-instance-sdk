## MODIFIED Requirements

### Requirement: `odcli update` supported scope

`odcli update` SHALL support only OdCLI installed as a `uv tool` from `maximchikAlexandr/odoo-instance-sdk`. The SDK SHALL expose `update_command()` returning `Command[UpdateResult]`; `PUBLIC_LEAF_CASES` SHALL set `sdk_primitive=update_command` (`cli_only_reason` forbidden). Expression SHALL NOT appear in this flow. Default `--ref` SHALL be `main`. `--dry-run` SHALL emit the frozen redacted plan and SHALL NOT launch a process, prompt, or mutate. CLI convenience SHALL delegate and SHALL NOT rebuild argv, cwd, environment, stdin, or actions. Unsupported provenance SHALL return `unsupported_install`. Source SHALL come from PEP 610 / uv-tool metadata, not cwd or the Odoo project remote.

Inspect SHALL be in-process ActionSteps. `PUBLIC_LEAF_CASES` SHALL contain two rows for the same Click path: `update` (`mutating-or-spawning`) and `update --check` (`process-previewable-read-only`), both `sdk_primitive=update_command`. When `--ref` is a 40-character lowercase hex SHA equal to installed `vcs_info.commit_id`, mutating `update` SHALL return `already_current` with zero ProcessSteps only when the canonical update journal and rollback snapshot are both absent. Otherwise the post-resolution update lifecycle SHALL capture, in execution order, `update.install`, `update.migrate`, and `update.verify.version` as immutable `internal/proc` ProcessSteps with argv known before mutation. `update.verify.version` SHALL use the target OdCLI absolute executable path and argv `(<target-odcli>, "--version")`, SHALL be read-only, and SHALL be consumed exactly once through the existing execution ledger. Maintenance SHALL run Alembic/`storage_migration.py` and write one JSON envelope v1 `UpdateResult` on stdout; the parent SHALL deserialize that document, consume the planned version check, and commit only after both succeed. Maintenance SHALL NOT call uv or re-enter install. There SHALL NOT be a second Click command for maintenance.

#### Scenario: unsupported install is refused

- **WHEN** `odcli update` runs on a pipx/system/editable/unknown install
- **THEN** it returns `unsupported_install` with `manual_argv` (`tuple[str, ...] | None`) if known and changes nothing

#### Scenario: source comes from installed provenance

- **WHEN** `odcli update` runs on a supported uv-tool install
- **THEN** the source is determined from `direct_url.json`/uv tool metadata, not from cwd or the Odoo project Git remote

#### Scenario: verify process is frozen before execution

- **WHEN** an immutable-target update command is built or rendered by `odcli update --dry-run`
- **THEN** its public plan contains read-only ProcessStep `update.verify.version` with the captured target executable and `--version`
- **AND** running the command consumes that exact captured step once after maintenance and before commit

#### Scenario: ledger remains fail-closed

- **WHEN** update execution requests an unplanned, substituted, duplicated, or omitted process step
- **THEN** the existing execution ledger rejects the run under its normal parity and consumption rules
- **AND** update-specific code does not bypass or relax the ledger

### Requirement: `odcli update` nine-phase flow

`odcli update` SHALL run nine phases: Inspect, Resolve, Preflight, Quiesce, Snapshot, Install, Migrate-in-a-new-process, Verify, Commit/Cleanup. It SHALL be a coordinating process and SHALL NOT continue running old Python code by importing target-revision modules after replacing its own environment.

1. **Inspect** — in-process `read_uv_tool_direct_url()` plus `sys.executable` and uv-tool layout; check `uv` presence, write-access, supported provenance, and canonical update recovery state.
2. **Resolve** — mutating `--dry-run` SHALL NOT spawn. For a mutable branch or tag, `--check` SHALL run one read-only ProcessStep `("git", "ls-remote", "--exit-code", "https://github.com/maximchikAlexandr/odoo-instance-sdk.git", "refs/heads/<ref>", "refs/tags/<ref>", "refs/tags/<ref>^{}")`; an annotated tag SHALL resolve to its peeled commit. A non-zero exit or output without a full commit SHA SHALL return `unsupported_install` with a concrete diagnostic and manual install argv. A 40-character lowercase hex `--ref` is already immutable and SHALL require no resolver process. Mutating `already_current` without uv happens only when that SHA equals installed `vcs_info.commit_id` and no incomplete recovery state exists.
3. **Preflight** — check Python/platform compatibility, `shutil.disk_usage(get_user_root()).free` minus `max(1 GiB, 10%)`, user-data schema, and the full migration path to target before any mutation. Shortfall is `preflight_failed`. Preflight SHALL NOT spawn uv.
4. **Quiesce** — acquire `exclusive_lock` on `get_locks_dir()/odcli-update.lock`. On conflict, return the existing lock error with that path. The Odoo runtime SHALL NOT be stopped unless a migration explicitly requires it.
5. **Snapshot** — save a verifiable rollback snapshot of affected metadata: current exact install requirement/ref, package revision, SQLite catalog, and other files actually touched by the migration plan. Large backup/filestore data SHALL NOT be copied without need.
6. **Install** — run `update.install` through `internal/proc` with `shell=False`. When `--ref` is a 40-char SHA this is an exact SHA install; when it is `main` or a tag, uv resolves inside this one install without rebuilding later argv.
7. **Migrate in a new process** — run `update.migrate` as `(odcli, "update", "--format", "json")` with `ODCLI_MAINTENANCE=1`. The old interpreter SHALL NOT import new modules over itself. Journal `get_user_root()/update/journal.json` SHALL retain frozen fields `phase`, `target_ref`, `snapshot_sha`, `maintenance_pid`. Snapshot SHALL remain at `get_user_root()/update/snapshot/`.
8. **Verify** — after a successful maintenance result, run the planned `update.verify.version` target executable once through the parent command ledger and verify the expected target SHA/result before declaring success. No second version subprocess SHALL run inside maintenance or outside the ledger.
9. **Commit/Cleanup** — mark success only after the planned verify succeeds; delete the journal and snapshot directory only then.

`--dry-run` SHALL NOT launch a process. For a mutable branch or tag, `--check` SHALL be the only Resolve spawn; an exact SHA requires none. Passing `--check` and `--dry-run` together SHALL be a Click usage error with exit code 2 before SDK resolution.

#### Scenario: already current is a no-op for an exact SHA

- **WHEN** `--ref` is a 40-character lowercase hex SHA equal to installed `vcs_info.commit_id` and canonical journal/snapshot inspection finds no incomplete state
- **THEN** `already_current` is returned with zero ProcessSteps and no migrations or reinstall run

#### Scenario: migrations run in a new process

- **WHEN** the install phase completes
- **THEN** migrations are executed by a new subprocess OdCLI, not by the old loaded interpreter

#### Scenario: dry-run shows the complete post-install plan

- **WHEN** `odcli update --dry-run` renders an immutable-target update
- **THEN** the install, migration, and `update.verify.version` process steps are shown in execution order
- **AND** the uv tool, catalog, journal, snapshot, and project files are unchanged

#### Scenario: successful verification commits and cleans recovery state

- **WHEN** maintenance succeeds and planned `update.verify.version` exits successfully for the target revision
- **THEN** the coordinator commits the update and removes the journal and rollback snapshot
- **AND** the result is `updated` with the final target SHA and cleared recovery state

#### Scenario: interrupted execution preserves evidence

- **WHEN** execution stops after install or migrate and before successful verify and commit
- **THEN** the journal and rollback snapshot remain present
- **AND** matching installed and target SHAs do not convert that state into `already_current`

### Requirement: `odcli update` UX and typed result

`odcli update` SHALL support Rich, JSON, and TOON with one typed contract. The result SHALL contain at least: `outcome` (`updated`, `already_current`, `unsupported_install`, `preflight_failed`, `rolled_back`, `update_incomplete`); source repository; previous/target/final version and full commit SHA; executable/tool-environment path in a safe normalized form; executed/skipped migration IDs and final schema versions; snapshot/journal state; rollback outcome; one concrete next step; and per-phase duration without secrets and without GitHub credentials/tokens.

Interactive confirmation SHALL be required before changing the install and data. `--yes` SHALL permit non-interactive execution after a successful preflight. `--no-input` without `--yes` SHALL exit before mutations. `odcli update --check` SHALL be the read-only variant with the `git ls-remote` ProcessStep defined by Resolve for mutable refs and no resolver process for an exact SHA. Before deriving `already_current`, `--check` SHALL inspect the canonical journal path and snapshot directory used by execution. A valid unfinished journal and preserved snapshot SHALL take precedence over installed/target SHA equality and SHALL return `update_incomplete`, `journal_state="present"`, `snapshot_state="present"`, and exactly one frozen `recovery_argv` equal to `(<absolute-odcli>, "update", "--ref", <journal-target-ref>, "--yes")`; `next_step` SHALL direct the caller to execute that argv to resume. A recorded maintenance PID that is no longer alive SHALL be classified as stale incomplete evidence and SHALL NOT make the recovery state absent or active. `--check` SHALL NOT delete, rewrite, resume, or lock the recovery state. `--dry-run` SHALL be a full plan without spawning. No phase SHALL log GitHub credentials, environment secrets, project passwords, or private config contents.

#### Scenario: check is read-only without recovery state

- **WHEN** `odcli update --check` runs and the canonical journal and snapshot are absent
- **THEN** installed and target version/full SHA are shown and nothing changes

#### Scenario: check reports interrupted update before SHA equality

- **WHEN** `odcli update --check` finds a valid unfinished journal and rollback snapshot after install or migrate
- **THEN** it returns `update_incomplete` with `journal_state="present"`, `snapshot_state="present"`, the journal target SHA, and the exact frozen resume `recovery_argv`
- **AND** it does so even when the installed SHA equals the target SHA
- **AND** it makes no filesystem or process mutation

#### Scenario: dead maintenance PID remains incomplete

- **WHEN** the unfinished journal records a maintenance PID that is not alive
- **THEN** `odcli update --check` reports stale `update_incomplete` state and the supported resume argv
- **AND** it does not report an active concurrent operation or absent recovery state

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
