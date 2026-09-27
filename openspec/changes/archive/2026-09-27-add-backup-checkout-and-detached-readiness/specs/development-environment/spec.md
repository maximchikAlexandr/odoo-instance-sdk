## ADDED Requirements

### Requirement: COPY checkout accepts an existing catalog backup

The public environment checkout operation MUST accept an optional complete backup UUID for COPY mode. The input MUST be mutually exclusive with live-source backup creation and MUST be captured in the same immutable command snapshot as the rest of checkout.

Planning MUST resolve the backup through the existing catalog and apply the current restore preconditions before mutation. Execution MUST reuse the existing source-neutral archive evidence, verified-snapshot restore transport, and COPY-journal pipeline without contacting source Odoo, creating another backup, or introducing a parallel extractor/verifier.

#### Scenario: Checkout restores the selected backup

- **WHEN** a caller requests COPY checkout for a new environment with an available compatible backup UUID
- **THEN** the plan identifies that exact backup and the existing restore/cleanup actions
- **AND** execution creates the environment with the backup's database and filestore
- **AND** returns the established typed environment result

#### Scenario: Backup input is invalid or incompatible

- **WHEN** the backup input conflicts with checkout mode, conflicts with live-source backup creation, or fails catalog/restore validation
- **THEN** checkout fails before creating the target environment
- **AND** returns the established typed error without exposing secrets

### Requirement: Checkout MUST preserve borrowed backups

Existing COPY-journal state MUST distinguish `owned`, `borrowed`, and migrated `unknown` backup ownership. A local-source archive created solely for that environment MUST be `owned`; a selected pre-existing or named-remote archive MUST be `borrowed`. Rollback and environment removal MUST delete a backup only when journal ownership is `owned`; borrowed or unknown-ownership backups MUST be retained.

#### Scenario: Environment cleanup retains its input backup

- **GIVEN** an environment was checked out from an existing catalog backup
- **WHEN** checkout rolls back or the environment is removed
- **THEN** owned target resources are cleaned according to current policy
- **AND** the selected backup and its catalog state are preserved

### Requirement: COPY checkout accepts an explicitly named remote

Checkout SHALL accept an exact remote name, mutually exclusive with an existing backup UUID or local source database, and only in COPY mode. It SHALL preflight the local target and source configuration, download one backup from that source, then restore directly into the new local environment without an intermediate baseline database or project-default change. Explicit backup/remote input SHALL bypass unrelated project-default freshness work. Source failure SHALL NOT trigger fallback to another source or cached backup.

#### Scenario: Isolated staging checkout

- **WHEN** checkout selects staging
- **THEN** it downloads only staging and restores into a new local database and independently writable filestore
- **AND** lab, staging, existing local environments and the project default remain unchanged

#### Scenario: Retry with retained backup

- **WHEN** a downloaded backup survives a later checkout failure
- **THEN** the error identifies its UUID and retained target resources
- **AND** a caller can deliberately use that UUID for a subsequent new checkout without contacting the remote

### Requirement: Explicit-source checkout has deterministic provenance

Named-source checkout SHALL default its base to that source's declared Git branch and resolve an exact available local commit before download. Known branch mismatch SHALL fail before mutation. A missing Git ref SHALL fail with corrective guidance, without hidden pull/merge or fallback. Retained-backup checkout SHALL reuse existing branch-match rules: unknown provenance requires an explicit base and returns an unknown warning.

Plan/results SHALL expose project, nullable historical source name, normalized origin/database, declared branch, resolved base commit and exact backup UUID when known. Declared branch SHALL NOT be represented as proof of the deployed remote commit. Changed source configuration after planning SHALL cause a stale-plan error.

#### Scenario: Staging overrides project default base

- **WHEN** project default base is develop and selected staging declares staging
- **THEN** checkout captures staging's resolved commit rather than develop

#### Scenario: Explicit base conflicts

- **WHEN** the caller requests a base known not to match the source backup's branch
- **THEN** checkout refuses before creating worktree, backup or database

#### Scenario: Legacy backup has unknown provenance

- **WHEN** the selected retained backup has no branch metadata
- **THEN** checkout requires an explicit base and reports branch compatibility as unknown

### Requirement: Explicit backup restore retains existing safety checks

Before local restore, checkout SHALL check catalog/file identity, checksum, archive safety, available disk reserve, local target absence and known Odoo major-version compatibility. It SHALL protect the input archive from concurrent deletion during validation/restore. Known version mismatch SHALL fail; unavailable version evidence SHALL be reported as unknown. Target neutralization and restore postconditions SHALL succeed before ready. Errors SHALL identify retained artifacts and an applicable existing recovery action without exposing secrets.

#### Scenario: Corrupt or incompatible backup

- **WHEN** a retained backup is corrupt or has a known incompatible Odoo major version
- **THEN** no target database is restored and the input backup is not deleted

#### Scenario: Neutralization fails

- **WHEN** restore created a target but neutralization fails
- **THEN** checkout does not mark the environment ready and reports its recoverable state and retained input UUID

## MODIFIED Requirements

### Requirement: `copy` DB mode

COPY mode MUST select exactly one source: existing local source database, explicit named remote, or exact catalog backup UUID. The default local-source path MUST create a ZIP with filestore through the existing backup operation and record environment ownership. It MUST require a reachable local source HTTP endpoint; arbitrary remote URLs in a local source config remain rejected. The existing caller-owned local-archive restore source SHALL NOT become a fourth COPY checkout input.

The explicit remote path MUST download through named-source preparation and retain that backup independently of environment ownership. The retained-backup path MUST skip source HTTP/download entirely. Both SHALL restore to the configured local project cluster, not the remote cluster. All paths MUST create a new target with copied filestore and neutralization, verify target existence and neutralization before ready, and never overwrite/reuse an existing target on retry.

#### Scenario: Copy checkout success

- **WHEN** local COPY checkout selects an available local source and a new target
- **THEN** it records an owned backup, restores and neutralizes the target and marks ready after postconditions

#### Scenario: Source Odoo unavailable

- **WHEN** local COPY requires an unavailable source HTTP endpoint
- **THEN** checkout fails with auditable recovery state and can be cleaned through existing removal

#### Scenario: Remote source Odoo refused

- **WHEN** the local-source path receives a non-local source HTTP endpoint without a named remote selector
- **THEN** checkout rejects it under the existing local-only constraint

#### Scenario: Explicit named remote source

- **WHEN** COPY explicitly selects a configured remote
- **THEN** only backup acquisition contacts that remote, and target mutation remains local

### Requirement: `--source-db` inference

Local-source checkout SHALL infer the source only when the Odoo config contains exactly one database; missing or multiple names in COPY mode SHALL require explicit `--source-db`. An explicit remote selector SHALL use that profile's database instead; an exact backup UUID SHALL use its catalog provenance. Those explicit inputs SHALL NOT require or infer a local source database and SHALL reject a simultaneous local source option.

#### Scenario: Single db_name inferred

- **WHEN** local-source checkout has exactly one configured database
- **THEN** it is inferred as the source

#### Scenario: Multiple db_names without flag — error

- **WHEN** local-source checkout has multiple configured databases and no explicit source
- **THEN** it fails with guidance to supply `--source-db`

#### Scenario: Empty db_name in copy mode without flag — error

- **WHEN** local-source COPY has neither configured nor explicit source database
- **THEN** it fails before target mutation

#### Scenario: Offline retained backup

- **WHEN** COPY selects an exact backup UUID without a configured local source database
- **THEN** it resolves the source from catalog provenance without requesting `--source-db` or contacting source Odoo
