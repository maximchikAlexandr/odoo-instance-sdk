## MODIFIED Requirements

### Requirement: `copy` DB mode

COPY mode MUST select exactly one source: existing local source database, explicit named remote, or exact catalog backup UUID. The default local-source path MUST create a ZIP with filestore through the existing backup operation and record environment ownership. It MUST require a reachable local source HTTP endpoint; arbitrary remote URLs in a local source config remain rejected. The existing caller-owned local-archive restore source SHALL NOT become a fourth COPY checkout input.

The explicit remote path MUST download through named-source preparation and retain that backup independently of environment ownership. The retained-backup path MUST skip source HTTP/download entirely. Both SHALL restore to the configured local project cluster, not the remote cluster. All paths MUST create a new target with copied filestore and neutralization, verify target existence and neutralization before ready, and never overwrite/reuse an existing target on retry.

Before creating owned checkout artifacts, every COPY source SHALL use the existing preflight target lookup and SHALL reject an existing target with `DatabaseAlreadyExistsError`. During execution, the checkout coordinator SHALL consume the immutable `database.restore.exists-before` step exactly once immediately before restore and SHALL consume `database.restore.exists-after` exactly once after restore. Selected catalog backups and named remotes SHALL NOT perform an additional nested target lookup that aliases either planned restore probe. Dry-run and execution SHALL use the same ordered command snapshot and SHALL NOT add a third existence probe or a source-specific execution context.

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

#### Scenario: Selected backup consumes the planned probes once

- **WHEN** COPY checkout executes from an available catalog backup into an absent target
- **THEN** it consumes `database.restore.exists-before` once, restores the selected backup, consumes `database.restore.exists-after` once, and reaches the established ready result
- **THEN** its successful dry-run and execution use the same immutable ordered plan without duplicate consumption

#### Scenario: Named remote uses the coordinator-owned probes

- **WHEN** COPY checkout executes from an explicitly named remote into an absent local target
- **THEN** backup acquisition is followed by the same single coordinator-owned before/after restore probe pair without a nested existence call consuming either step early

#### Scenario: Existing target remains protected

- **WHEN** any COPY source resolves to a target database that already exists during artifact-free preflight
- **THEN** checkout raises `DatabaseAlreadyExistsError` before restore and does not overwrite the target or mutate the selected/borrowed backup
