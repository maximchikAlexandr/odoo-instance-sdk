## ADDED Requirements

### Requirement: Declarative source Git branch provenance

`instance.databases.backup()` SHALL accept an optional keyword-only `source_git_branch: str | None = None`. It SHALL pass the caller-supplied value into the existing pre-request catalog audit row and return it on `Backup`. The method SHALL treat the value as declarative metadata and SHALL NOT run Git, call a remote provenance endpoint, or derive a branch from the database name.

Normalization SHALL trim surrounding whitespace, reject empty/non-empty-after-trim violations and control characters, and otherwise preserve the declared ref text. Omitting the option SHALL remain backward compatible and store `NULL`.

#### Scenario: Backup records configured branch

- **WHEN** backup is called with `source_git_branch="release/19"`
- **THEN** the catalog audit begins before HTTP with that branch and the returned backup exposes the same value

#### Scenario: Existing callers omit branch

- **WHEN** an existing caller invokes `backup(database_name)` without the new option
- **THEN** download behavior is unchanged and `Backup.source_git_branch is None`

#### Scenario: Invalid branch rejected before request

- **WHEN** `source_git_branch` is empty after trimming or contains a control character
- **THEN** backup fails before catalog or HTTP mutation
