## ADDED Requirements

### Requirement: Non-database session-store effects are honest command mutations

An SDK command that creates or deletes an Odoo browser session SHALL declare `mutating=True` and SHALL expose honest secret-file/session-store create, read, and cleanup `ActionStep` values even when its PostgreSQL transaction uses `commit=False`. Its command sibling SHALL capture one immutable argv, environment, runtime identity, timeout, fixed script, and private handoff contract; its convenience method SHALL only delegate and run that snapshot.

#### Scenario: Preview browser session creation

- **WHEN** a browser-session command is built or inspected without execution
- **THEN** no child process, temporary file, session-store entry, database change, lock, or audit mutation occurs
- **AND** the plan reports safe mutation categories without a SID, token, or secret-file content

#### Scenario: Execute browser session creation

- **WHEN** the captured command is run
- **THEN** its only process launch uses `internal/proc` with argv and `shell=False`
- **AND** no HTTP module owns a parallel subprocess seam or rebuilds the command
