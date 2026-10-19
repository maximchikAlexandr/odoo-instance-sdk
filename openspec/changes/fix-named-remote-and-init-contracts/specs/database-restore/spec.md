## ADDED Requirements

### Requirement: Generated configuration publication preserves captured Git validation

Main-checkout Compose restore SHALL validate generated configuration tracking through its immutable command snapshot before changing the project default. Planning SHALL capture the exact read-only repository-root and tracked-path commands. Execution SHALL consume those captured results rather than launching Git from a mutation action. Existing refusal of tracked generated files, Git errors, and repository-root drift SHALL remain authoritative, as SHALL rollback, external-source configuration, filestore, retained-artifact, and secret-redaction behavior.

#### Scenario: Captured validation allows the default switch

- **WHEN** a main-checkout restore is ready to publish and captured Git steps prove the generated config untracked in the same repository root
- **THEN** execution SHALL consume those steps before manifest/config publication and SHALL NOT launch an unplanned Git command

#### Scenario: Captured validation refuses unsafe publication

- **WHEN** the generated config is tracked, a Git check failed, or the repository root drifted
- **THEN** restore SHALL prevent manifest/config publication, keep the previous default active, and preserve existing typed retained-artifact evidence without exposing secrets
