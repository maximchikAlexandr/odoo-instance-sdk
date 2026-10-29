## ADDED Requirements

### Requirement: Planned filesystem consequences are structured and executable

An `ActionStep` for a known user-visible filesystem consequence SHALL expose an ordered `details.file_effects` array in the existing redacted public plan. Each effect SHALL identify `create`, `modify`, `replace`, `delete`, or `move`, its absolute normalized file or directory path, an optional move destination, and any condition or uncertainty needed to avoid overstating the effect. Execution SHALL consume the same captured target or fail/revalidate at the existing stale-plan boundary; it SHALL NOT silently apply the operation to a different target. Existing `ExecutionPlan` and `ActionStep` fields SHALL remain compatible, and one canonical redacted projection SHALL serve Rich, JSON, TOON, and fingerprints.

#### Scenario: Known file is replaced
- **WHEN** planning proves that a user-owned destination file exists and successful execution will atomically publish a replacement
- **THEN** the action exposes `replace` with the final destination and execution publishes only that captured destination after its prerequisite steps succeed

#### Scenario: Directory root is sufficient
- **WHEN** an owned worktree, virtual environment, filestore, or draft directory contains many recursive members
- **THEN** the plan exposes the root directory and operation without recursively enumerating its contents

#### Scenario: Exact target is not yet knowable
- **WHEN** execution-dependent data prevents planning from proving an exact child name or final action
- **THEN** the effect exposes the known containing directory, a condition, and a non-empty uncertainty reason and does not fabricate a guaranteed path or action

#### Scenario: Planned state changes before mutation
- **WHEN** a path identity, existence classification, or ownership fact required by the frozen effect differs at execution
- **THEN** execution revalidates under the capability's existing safety contract or fails stale before mutating an unplanned target

#### Scenario: Secrets remain absent
- **WHEN** paths, conditions, errors, or private prepared values contain registered secrets or sensitive configuration data
- **THEN** every public plan, fingerprint, Rich line, JSON value, TOON value, and progress event uses the single redacted projection and contains no secret or file content

### Requirement: File consequences cover the mutating command inventory

Applicable dry-runs SHALL describe user-visible filesystem consequences for translations export, init, `vscode generate --write`, environment create/checkout/sync/removal, database restore/refresh/drop, backup deletion, bug-report init/submit, update, Git commit/absorb/sync, and managed run/shell/exec/eval/test/module operations. Inapplicable effects SHALL be omitted rather than presented as mandatory. Internal temporary files, OdCLI logs, SQLite journals, synchronization locks, package-manager internals, PostgreSQL/pgAdmin service files, and `postgres approve-image` SHALL remain excluded unless a user-owned dependency lock or retained resource is itself the subject of the operation.

#### Scenario: Project files are classified
- **WHEN** `init --dry-run` resolves `.odcli/project.toml`, `.odcli/.gitignore`, and applicable Compose `.odcli/odoo.conf` and `.odcli/.env`
- **THEN** each applicable file is classified as create, modify, or unchanged, and unchanged files are not claimed as mutations

#### Scenario: VS Code generation does not write
- **WHEN** `vscode generate` runs without `--write`
- **THEN** the plan does not claim a `.vscode` directory or `launch.json` mutation

#### Scenario: VS Code existing file is refused
- **WHEN** `vscode generate --write` resolves an existing `launch.json` under the existing refusal rule
- **THEN** the plan reports the protected path and refusal condition and execution does not claim or perform replacement

#### Scenario: Environment ownership changes the plan
- **WHEN** environment create, checkout, sync, or removal selects an owned versus reused Python environment, lock file, backup, or filestore
- **THEN** the effects identify only resources the selected mode writes or deletes and state the preservation/rollback conditions for the others

#### Scenario: Restore rollback paths are visible
- **WHEN** restore or refresh can move a prior filestore aside before publication
- **THEN** the plan exposes source and destination of the conditional move plus the conditions for later deletion or restoration

#### Scenario: Database drop retains unowned filestore
- **WHEN** database removal cannot prove ownership of the corresponding filestore
- **THEN** the plan reports its known path and preservation condition and does not claim deletion

#### Scenario: Backup deletion keeps existing facts
- **WHEN** a retained backup deletion is planned
- **THEN** its path, presence, size, and relationships remain available together with the standardized delete or retain consequence

#### Scenario: Git outcome is conflict-dependent
- **WHEN** commit, absorb, or sync can affect index, history, worktree, rebase, or push state but final files depend on execution or conflicts
- **THEN** the plan describes the known Git scope and uncertainty and does not promise an exact final file list

#### Scenario: Arbitrary code limits prediction
- **WHEN** run, shell, exec, eval, test, module test, or module update can execute arbitrary Odoo or Python code
- **THEN** the plan exposes only confirmed managed configuration, logfile, and data roots and explicitly states that arbitrary code effects are not predicted

#### Scenario: Update snapshot is not an Odoo backup
- **WHEN** self-update plans its metadata snapshot and possible OdCLI data migration
- **THEN** the plan labels that scope as OdCLI installation/data recovery and does not represent it as a database or filestore backup

### Requirement: Machine progress reuses planned lifecycle events

Clone, backup-download, and restore execution SHALL report machine-readable stage events through the existing planned-step observer. Events SHALL be versioned, identified, sanitized, and emitted on stderr while the final JSON/TOON result remains exactly one stdout document. Reliable byte progress SHALL expose monotonic downloaded bytes, optional total bytes, byte units, and a non-negative measured rate; an unknown total SHALL omit percentage rather than invent it. Dry-run SHALL emit no runtime events.

#### Scenario: JSON download has a known size
- **WHEN** a JSON-mode backup download receives a trustworthy content length
- **THEN** stderr emits complete JSON progress records with downloaded bytes, total bytes, measured rate, and attributable stage identity while stdout contains only the final JSON envelope

#### Scenario: TOON download has an unknown size
- **WHEN** a TOON-mode download cannot prove the total size
- **THEN** each stderr TOON event is Record-Separator framed, reports downloaded bytes without total or percentage, and stdout contains only the final TOON envelope

#### Scenario: Restore changes stages
- **WHEN** restore advances through acquisition, database import, filestore publication, verification, and cleanup stages
- **THEN** machine events preserve the planned step order and identity and never include archive contents, credentials, or attachment contents

#### Scenario: Progress fails or is interrupted
- **WHEN** a reported long operation fails or receives an interrupt
- **THEN** its active event closes as failed/interrupted, owned resources follow existing cleanup, and the final exit/result contract remains authoritative
