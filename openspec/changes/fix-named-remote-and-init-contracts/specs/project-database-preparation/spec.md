## ADDED Requirements

### Requirement: Named remote database resolution is deferred and singular

A named remote source SHALL permit an absent database while requiring every explicit database to be a nonblank string. Registration SHALL NOT list databases. Refresh SHALL pass the nullable configured value to the existing resolver: an explicit value SHALL bypass listing; otherwise exactly one listed name SHALL be selected. Empty, multiple, or unavailable listings SHALL return their distinct typed errors before backup. The selected database SHALL retain the named source, password, URL, branch provenance, and backup identity through materialization and SHALL NOT be persisted into the source configuration as a registration side effect.

#### Scenario: Explicit database bypasses discovery

- **WHEN** refresh selects a named source with a valid explicit database
- **THEN** it SHALL use that database without listing remote databases and SHALL preserve the named-source password and provenance

#### Scenario: Single database is resolved at refresh

- **WHEN** refresh selects a named source with no database and the remote list contains exactly one valid name
- **THEN** it SHALL use that name for the backup and retain the named-source password and provenance

#### Scenario: Unsafe discovery results fail before backup

- **WHEN** the database list is empty, contains multiple names, or cannot be obtained
- **THEN** refresh SHALL return the corresponding typed none, ambiguous, or unavailable error before any backup request

### Requirement: COPY planning requires an already selected remote database

COPY checkout SHALL NOT add a second database-discovery path. A named source with no configured database SHALL fail planning with an actionable `ConfigError` that identifies the supported alternatives: perform refresh and use its retained backup UUID, or configure an explicit database. No checkout artifact, HTTP request, backup, or database mutation SHALL occur.

#### Scenario: COPY source has no selected database

- **WHEN** COPY checkout selects a named remote whose database is absent
- **THEN** planning SHALL fail before mutation with the typed guidance and SHALL NOT raise `AssertionError`

### Requirement: Default publication consumes captured generated-config Git checks

Main-checkout Compose preparation SHALL capture the exact read-only Git repository-root and tracked-path checks needed to validate an existing generated configuration. Execution SHALL consume those captured steps before publishing the generated config or manifest. A tracked configuration, Git failure, or changed repository root SHALL preserve the previous default and prevent successful publication while retaining existing typed recovery evidence for any completed database or filestore work.

#### Scenario: Captured Git checks permit publication

- **WHEN** restore completed and the captured Git checks prove the generated config is untracked in the same repository root
- **THEN** default publication SHALL consume those checks and may publish the new binding through the existing atomic path

#### Scenario: Captured Git checks reject publication

- **WHEN** the generated config is tracked, a captured Git check fails, or the repository root changes before execution
- **THEN** publication SHALL fail without an unplanned subprocess, preserve the prior default, and SHALL NOT expose secrets
