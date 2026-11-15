# Spec Delta

## ADDED Requirements

### Requirement: Optional Odoo logging section
`ProjectConfig` SHALL accept an optional `[logging.odoo]` table and SHALL omit it when it is absent. The table SHALL accept `structured` as a boolean, `level` as a nonblank log level, and `handlers` as a list of nonblank `logger:LEVEL` entries. Invalid types or blank entries SHALL fail manifest load. Writing a config with no logging intent SHALL NOT add the table. Secrets SHALL NOT be accepted in this table.

#### Scenario: Missing section preserves the manifest
- **WHEN** a manifest has no `[logging.odoo]` table and is loaded and written
- **THEN** the written manifest still has no `[logging.odoo]` table

#### Scenario: Structured settings round-trip
- **WHEN** a manifest sets `structured = true`, `level = "info"`, and one handler entry
- **THEN** the loaded config preserves those values and the written table contains them

#### Scenario: Invalid logging section
- **WHEN** `structured` is not a boolean or a handler entry is blank
- **THEN** manifest load fails and the file is unchanged
