## ADDED Requirements

### Requirement: Inspectable database preparation command

Project database preparation/refresh SHALL expose one command whose plan captures every existing psql, pg_restore, Odoo shell, and other child-process step before the first mutation, alongside honest action steps for HTTP/database/filesystem/catalog/lock work. Existing coordinator serialization, coalescing, retained-artifact reporting, and atomic default switching SHALL remain operation-specific.

#### Scenario: Refresh dry-run is inspected

- **WHEN** a caller builds a refresh command with restore and administrator reset enabled
- **THEN** its plan shows the captured restore and Odoo shell steps, commit/rollback intent, and relevant action steps
- **AND** no backup, database, config, catalog, or process mutation occurs

#### Scenario: Preparation fails after mutation

- **WHEN** execution fails after a database or retained backup has been created
- **THEN** the existing typed failure context and explicit compensation/retention rules apply
- **AND** failure is not hidden in a generic pipeline result
