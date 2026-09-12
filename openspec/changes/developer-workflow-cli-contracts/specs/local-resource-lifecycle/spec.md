## ADDED Requirements

### Requirement: Single global user-storage root
All OdCLI-owned global config, catalogue, environments, project runtime artifacts, backups, locks/state, pgAdmin, and other user data SHALL resolve below the real home directory's `.odcli` root; no command SHALL retain a platformdirs root, while repository-local `.odcli/` SHALL remain separate. [Source: GH#64 §7]

#### Scenario: Fresh installation
- **WHEN** OdCLI first creates global artifacts
- **THEN** every artifact is below `~/.odcli/` with existing security modes and no legacy root is created

### Requirement: Locked conflict-safe storage migration
Migration from all legacy data/cache/state roots SHALL be one-time, globally locked, idempotent, interruption-safe, and fail closed with exact conflicts when source and destination disagree. It SHALL preserve identifiers, SQLite relations, paths, modes, and ownership; source data SHALL be removed only after verified destination success and recoverable progress. [Source: GH#64 §7]

#### Scenario: Conflicting destination
- **WHEN** both legacy and unified roots contain incompatible data
- **THEN** migration reports every conflicting path and overwrites or removes nothing

#### Scenario: Retry interrupted migration
- **WHEN** migration restarts after any recorded stage
- **THEN** it resumes or verifies completed moves without duplicates or catalogue corruption
