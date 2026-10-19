## ADDED Requirements

### Requirement: Init delegates PostgreSQL image consent to the existing trust store

Compose init SHALL use the existing public digest-resolution semantics and per-project user-data approval store. It SHALL approve only the exact resolved OCI RepoDigest for the selected reference, retain mode `0600`, and preserve the existing fail-closed digest re-resolution before startup. Init SHALL NOT create a second trust store, accept a mutable tag as consent, or apply image consent to external PostgreSQL.

#### Scenario: Init approval is valid for startup

- **WHEN** init persists consent for the exact resolved reference and digest
- **THEN** subsequent Compose startup SHALL use the same existing approval record and SHALL still re-resolve and reject digest drift

#### Scenario: External PostgreSQL has no image approval

- **WHEN** init selects external PostgreSQL
- **THEN** no digest resolution or approval operation SHALL be planned or executed

#### Scenario: Corrupt or changed approval remains fail-closed

- **WHEN** the stored approval is corrupt or the reference later resolves to a different digest
- **THEN** the existing lifecycle SHALL refuse startup until the exact digest is approved
