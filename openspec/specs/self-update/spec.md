# self-update Specification

## Purpose
Define a provenance-aware, recoverable self-upgrade flow for OdCLI uv-tool installations, including immutable revision resolution, migration, verification, and rollback contracts.
## Requirements
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

### Requirement: Alternate launchers reject direct self-update
The update SDK primitive and CLI adapter SHALL detect numbered and hot-fix contexts before check, dry-run, maintenance, recovery, or mutation planning. A numbered context SHALL direct the operator to the developer-only skill and repository-owned private service workflow for exact-SHA replacement; it SHALL NOT direct the ordinary user skill, mention, or require a public numbered-management surface. A hot-fix context SHALL direct the agent to skill-managed canonical update/reconciliation and SHALL NOT offer revision replacement. Rejection SHALL NOT acquire update locks, resolve refs, snapshot metadata, run uv, execute migrations, or change any installation, checkout, or state root.

#### Scenario: Numbered update is rejected
- **WHEN** `odcli-N update` is invoked with any supported update arguments
- **THEN** it exits nonzero with private service-tool guidance and changes neither numbered nor canonical tool, checkout, database, filestore, port, or state

#### Scenario: Hot-fix update is rejected
- **WHEN** `odcli-fix-ISSUE update` is invoked
- **THEN** it exits nonzero with skill-managed reconciliation guidance and changes no canonical, numbered, or hot-fix installation or state

#### Scenario: Canonical update remains available
- **WHEN** ordinary `odcli update` runs without an alternate context
 - **THEN** the existing self-update/recovery contract remains unchanged and it does not discover, replace, or remove alternate launchers, roots, or linked checkouts

### Requirement: Self-update preserves installed optional extras

For a supported uv-tool installation, inspect/snapshot SHALL capture the normalized installed direct requirement and requested optional-extra set from uv tool metadata. Install, automatic rollback, and emitted recovery argv SHALL use the same validated package source and exact extra set at their respective target/snapshot revision. `odcli update` SHALL NOT add extras to a core-only install or drop `[dashboard]` from an install that has it; missing, malformed, or ambiguous installation-composition evidence SHALL fail preflight before replacement.

#### Scenario: Dashboard installation updates
- **WHEN** installed provenance identifies `odoo-instance-sdk[dashboard]` from the supported Git source
- **THEN** target install and any rollback/recovery requirement include `[dashboard]` and the resulting tool retains FastAPI, Starlette, and Uvicorn

#### Scenario: Core-only installation updates
- **WHEN** installed provenance identifies the supported package without optional extras
- **THEN** update preserves the core-only composition and does not install dashboard dependencies

#### Scenario: Extra provenance is ambiguous
- **WHEN** uv-tool metadata cannot prove the installed package requirement or extra set
- **THEN** update returns a preflight failure before `uv tool install` and supplies a sanitized manual/recovery next step

### Requirement: Updated dashboard installation is verified

When the preserved installation includes `dashboard`, post-install maintenance SHALL verify in the new interpreter that the declared dashboard dependencies import and the packaged SPA entry assets exist before update commit/cleanup. An isolated regression SHALL update a dashboard-enabled uv-tool installation to an exact target revision, restart `odcli monitor --headless`, and prove its health/API response without a manual reinstall.

#### Scenario: Dashboard dependency was lost
- **WHEN** target replacement lacks a required dashboard import or packaged SPA entry asset
- **THEN** update verification fails under the existing rollback/incomplete-update contract and does not report success

#### Scenario: Monitor works after restart
- **WHEN** a dashboard-enabled update succeeds and the new `odcli monitor --headless` process is started
- **THEN** monitor starts without FastAPI/Starlette/Uvicorn import failure and its documented health/API request succeeds

### Requirement: Self-update preserves and verifies installed companion composition

Canonical `odcli update` SHALL inspect whether `odcli-multica` is installed as a supported uv tool. An absent companion SHALL preserve the existing core-only update plan and SHALL NOT install a companion. A present companion SHALL require unambiguous protected receipt, launcher, package revision, and embedded `odoo-instance-sdk` revision evidence before mutation.

For a present companion, the immutable update plan SHALL warn before mutation that both utilities will change, preserve the existing core install step, and capture an additional companion install step that installs `odcli-multica` from the exact `packages/odcli-multica` monorepo subdirectory and pins both that distribution and its isolated SDK dependency to the same target VCS revision. The versioned snapshot, journal, rollback, recovery argv, and incomplete-state result SHALL cover both uv-tool environments. Post-install verification SHALL invoke the captured companion launcher with a supported non-mutating command and SHALL read protected distribution metadata from its environment to prove the actual VCS revision of `odcli-multica` and its embedded SDK; it SHALL NOT require an unsupported companion option or accept matching display versions as revision evidence. Commit/cleanup and aggregate `updated` success SHALL occur only after the core launcher, companion launcher, companion distribution revision, and embedded SDK revision all verify. A partial failure SHALL roll back both compositions when safe or return `update_incomplete` with retained recovery evidence.

#### Scenario: Core-only installation remains core-only

- **WHEN** supported core uv-tool provenance is present and no `odcli-multica` launcher or receipt exists
- **THEN** update uses the existing core-only install, migrate, verify, rollback, and recovery flow and does not install the companion

#### Scenario: Installed companion and embedded SDK advance together

- **WHEN** protected provenance identifies installed core and companion uv tools and update targets an immutable revision
- **THEN** the frozen plan installs the companion from `packages/odcli-multica`, pins its embedded SDK to the same revision, and verifies both installed distribution revisions before success

#### Scenario: Companion launcher uses a supported readiness command

- **WHEN** a coordinated update verifies the captured `odcli-multica` launcher
- **THEN** it invokes a supported non-mutating command and does not require the unsupported `--version` option

#### Scenario: Users are warned before coordinated replacement

- **WHEN** a present companion makes update a coordinated two-tool mutation
- **THEN** Rich output identifies both affected utilities before the first install step and dry-run exposes both secret-free frozen install and verify steps

#### Scenario: Companion SDK remains old

- **WHEN** the companion launcher starts but its installed SDK distribution does not match the target revision
- **THEN** verification fails and update does not return `updated` or remove journal and snapshot evidence

#### Scenario: Companion package remains old

- **WHEN** the companion launcher starts but the installed `odcli-multica` distribution metadata does not match the target revision
- **THEN** verification fails and update does not return `updated` or remove journal and snapshot evidence

#### Scenario: Companion revision evidence is invalid

- **WHEN** the companion launcher succeeds but protected metadata is missing, malformed, ambiguous, or unreadable
- **THEN** verification fails closed and update retains the journal, snapshot, and recovery command

#### Scenario: Partial coordinated update is recoverable

- **WHEN** core replacement succeeds but companion install, launcher readiness, or revision verification fails
- **THEN** update restores and verifies both prior compositions when safe or returns `update_incomplete` with retained two-tool journal, snapshot, and recovery commands that retain the companion subdirectory requirement

#### Scenario: Present companion provenance is ambiguous

- **WHEN** an `odcli-multica` launcher exists but its receipt, package revision, tool environment, or embedded SDK revision cannot be proven uniquely
- **THEN** preflight fails before either tool is changed
