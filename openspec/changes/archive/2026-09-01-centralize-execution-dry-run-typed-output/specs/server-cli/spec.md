## ADDED Requirements

### Requirement: Captured server command execution

The server CLI execution primitive SHALL accept and execute a captured private process step through `internal/proc`. It SHALL preserve argv, cwd, sanitized environment, captured stdout/stderr, timeout, return code, and duration behavior without calling subprocess APIs directly.

#### Scenario: Captured server command completes

- **WHEN** a server command is run through a recording executor
- **THEN** the recorded argv, cwd, environment policy, stdin, and timeout match its immutable plan after redaction
- **AND** the returned `CommandResult` preserves the existing fields
