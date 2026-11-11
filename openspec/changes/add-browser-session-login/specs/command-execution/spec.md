## ADDED Requirements

### Requirement: Browser-session commands expose honest effects without credentials

The SDK browser-session command SHALL reuse the shared immutable command and `internal/proc` boundary, SHALL declare filesystem session creation as mutating even with a rolled-back database transaction, and SHALL describe private-file create/read/delete actions without exposing SID or file contents in its plan, result, fingerprint, repr, events, recordings, stdout, stderr, or errors.

#### Scenario: Inspect without execution

- **WHEN** a browser-session command is built or inspected without execution
- **THEN** it reports fixed source, argv, runtime and safe mutation categories
- **AND** it creates no process, file, database effect, session-store entry, or credential projection

#### Scenario: Execute through the shared boundary

- **WHEN** the browser-session command runs
- **THEN** its only process launch uses the existing captured argv with `shell=False`
- **AND** the HTTP layer owns no subprocess or duplicate command implementation
