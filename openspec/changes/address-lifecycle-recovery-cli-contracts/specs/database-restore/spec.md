## ADDED Requirements

### Requirement: Replace an existing COPY environment from a retained backup

CLI-private/internal replacement orchestration SHALL reuse the existing catalogue-backup validation, project PostgreSQL ownership, preparation/restore, postcondition, locking, progress, dry-run, confirmation, failure-context and audit primitives. Under canonical environment, backup and cluster locks, it SHALL revalidate a stopped, non-removed COPY environment, its exact target and absence of active target-database sessions before mutation. With no explicit force contract, it SHALL NOT terminate active sessions. It SHALL reject a caller-supplied `--target`, move the current proven-owned database and contained filestore to unique rollback names, restore the selected database and matching filestore to the unchanged exact target name, apply the existing requested admin-password reset, confirm the restored database and provenance, and only then remove the rollback artifacts. The environment ID, name, branch, worktree, generated config, HTTP binding, target database, and Python binding SHALL remain unchanged. Shared/source databases SHALL never be accepted by this route.

#### Scenario: Successful replacement preserves identity

- **WHEN** a stopped COPY environment and retained backup pass all planning and execution revalidation
- **THEN** its exact target database and filestore contain the selected backup, its environment identity/bindings are unchanged, and current provenance points to that backup

#### Scenario: Database and filestore match

- **WHEN** replacement reports success
- **THEN** both the database and filestore originate from the same validated backup and rollback artifacts are absent

#### Scenario: Dry-run is exact and inert

- **WHEN** replacement is invoked with `--dry-run`
- **THEN** the plan exposes sanitized exact process commands and honest database/filesystem/catalogue actions without locking for mutation or changing any resource

#### Scenario: Active target sessions block replacement

- **WHEN** any active target-database session is observed during planning or execution revalidation
- **THEN** replacement terminates no session and starts no database, filestore, catalogue or configuration mutation

#### Scenario: Exact recorded target is mandatory

- **WHEN** replacement receives the existing `--target` option even if it names the recorded database
- **THEN** it rejects the conflicting option before mutation instead of allowing target substitution

### Requirement: Replacement compensation and retry state

If replacement fails after the prior database or filestore is moved aside, including failure of a requested existing admin-password reset before final provenance commit, compensation SHALL remove only newly created artifacts whose ownership is proven and restore both prior rollback artifacts to the exact target names. Proven successful compensation SHALL leave the previous backup/provenance authoritative. If full compensation cannot be proven, the environment SHALL enter an explicit `cleanup_failed` retryable state, retain all known artifacts and sanitized identities, and SHALL NOT advertise the new backup as active. Repeating replacement or removal SHALL re-read and validate those retained identities before any mutation.

#### Scenario: Restore fails and compensation succeeds

- **WHEN** the new restore fails after the old database and filestore are moved aside and both can be restored safely
- **THEN** the previous usable database, filestore and provenance are restored and the new backup is not advertised as active

#### Scenario: Compensation is incomplete

- **WHEN** a replacement failure cannot safely restore every prior artifact or remove every partial new artifact
- **THEN** the environment records `cleanup_failed` with retained-artifact evidence and no false active-backup claim

#### Scenario: Concurrent identity changes

- **WHEN** any database, filestore, backup, cluster or environment identity differs at execution revalidation
- **THEN** replacement aborts before mutation or compensates the already-started stage without touching an unproven resource

#### Scenario: Requested admin-password reset fails

- **WHEN** the existing requested admin-password reset fails after restore but before final provenance commit
- **THEN** replacement applies the same compensation or `cleanup_failed` contract and does not advertise the selected backup as active
