## ADDED Requirements

### Requirement: Runtime ownership is environment or project

A foreground instance constructed from a ready environment SHALL persist runtime identity with `environment_id`; one constructed from an initialized project SHALL persist the same identity with `project_id` and no environment ID. Exactly one owner kind SHALL be present. Project ownership SHALL use canonical repository/project identity already recorded by project initialization and SHALL NOT synthesize an environment row. Manual instances SHALL remain unpersisted.

#### Scenario: Project foreground runtime is recorded
- **WHEN** a project-bound foreground Odoo process starts successfully
- **THEN** its PID, create time, start time, revision, URL, port, database, and project owner are persisted without an environment owner

#### Scenario: Ownership is exclusive
- **WHEN** any persisted runtime row is validated
- **THEN** exactly one of environment owner or project owner is present

### Requirement: Project runtime cleanup preserves stale-process safety

Project-owned runtime identity SHALL be cleared best-effort in the same foreground `finally` path as environment-owned identity. Readers SHALL validate PID create time and other existing identity checks before treating either owner kind as live.

#### Scenario: Project runtime exits
- **WHEN** a project-owned foreground process exits normally, fails, or is interrupted
- **THEN** its runtime identity is cleared without deleting project registration
