## ADDED Requirements

### Requirement: Project-local environment loading

After resolving an initialized project and before constructing project runtime operations, `odcli` SHALL look only for `<project-root>/.odcli/.env`; it SHALL NOT walk above the resolved project or load cwd-global dotenv files. Existing process variables SHALL override file values, and loaded values SHALL remain scoped to the current `odcli` process and explicitly allowed child environments. A missing file SHALL be ignored deterministically. An unreadable or malformed file SHALL fail with an actionable sanitized error before mutation. The parser SHALL support conventional unquoted and quoted `KEY=VALUE` assignments without shell evaluation, command substitution, or variable interpolation.

#### Scenario: Process environment wins
- **WHEN** `.odcli/.env` and the invoking process both define `ODCLI_TEST_MASTER_PASSWORD`
- **THEN** the existing process value is used and neither value is printed

#### Scenario: Search stops at project boundary
- **WHEN** an initialized project has no `.odcli/.env` but a parent directory does
- **THEN** the parent file is not loaded

#### Scenario: Malformed file fails before work
- **WHEN** the resolved project file contains an invalid assignment
- **THEN** the command fails before child creation or mutation without echoing the line's value

### Requirement: Project-local secret-file hygiene

Project initialization SHALL ensure `.odcli/.env` is covered by the repository ignore rules and documentation SHALL require owner-only readability. Loading an existing file with group/other permission bits SHALL fail closed with a path-only remediation message. Secret keys and values SHALL be excluded from Rich, JSON, TOON, dry-run plans, errors, diagnostics, logs, and fingerprints.

#### Scenario: Insecure permissions are refused
- **WHEN** `.odcli/.env` is readable or writable by group or others on a platform supporting POSIX mode bits
- **THEN** loading fails before use and advises owner-only permissions without revealing contents
