## ADDED Requirements

### Requirement: User-level backup retention settings

The SDK SHALL read and update `backup.retention_days` and `backup.auto_prune` in the existing platformdirs configuration `user.toml`. Defaults SHALL be 14 days and false. Age SHALL be a positive integer, not a boolean; enablement SHALL be a boolean. Project manifests SHALL NOT override automatic deletion policy. Public results SHALL expose effective values and the absolute settings path without unrelated configuration or secrets.

#### Scenario: First use and explicit enablement

- **WHEN** no retention settings exist
- **THEN** effective age is 14 days and automatic deletion is disabled
- **WHEN** the user enables it through the public SDK or CLI
- **THEN** an atomic update preserves unrelated user settings and subsequent operations use the new policy

#### Scenario: Invalid settings fail safely

- **WHEN** retention configuration is unreadable or malformed
- **THEN** explicit prune fails before deletion and opportunistic prune is skipped with a structured warning
- **AND** neither silently substitutes a deletion policy

#### Scenario: Preview policy update

- **WHEN** an update is previewed
- **THEN** the resolved settings and destination are shown without creating or modifying the file
