## ADDED Requirements

### Requirement: Same-snapshot preview approval and execution

A previewable machine mutation SHALL build exactly one `Command`, retain its private prepared snapshot in one bounded local process, emit only its redacted public plan and fingerprint, and execute that same `Command` only after receiving an approval carrying the identical fingerprint. The public plan SHALL NOT become an executable serialization and a second process SHALL NOT reconstruct the command from preview fields.

#### Scenario: Approved fingerprint executes captured command

- **WHEN** a session receives approval with the fingerprint it just emitted before the deadline
- **THEN** the original retained `Command` runs once with its captured callbacks and prepared steps

#### Scenario: Mismatched fingerprint is rejected

- **WHEN** approval carries a different fingerprint
- **THEN** execution does not start, the session emits a typed stale-approval error and all invocation-owned resources are cleaned up

### Requirement: Bounded decision and cancellation lifecycle

An approval session SHALL accept exactly one typed `approve` or `cancel` decision, enforce the descriptor's bounded timeout and maximum input size, and treat EOF, timeout, invalid sequence, explicit cancellation and interrupt as non-execution outcomes with deterministic typed records and exit semantics. Cleanup SHALL terminate only exact invocation-owned children and preserve all existing rollback and process-identity protections.

#### Scenario: Approval times out

- **WHEN** no valid decision arrives before the bounded deadline
- **THEN** the command is not run, a timeout record is emitted and the process exits without retained invocation-owned work

#### Scenario: Caller cancels preview

- **WHEN** the caller sends a valid cancel decision after preview
- **THEN** the command is not run, one cancelled terminal record is emitted and cleanup completes

#### Scenario: Execution precondition still fails safely

- **WHEN** approval is valid but an execution-time ownership, Git/config/runtime, port, filesystem, archive, database or lock precondition no longer holds
- **THEN** the existing prepared command fails through its typed error/rollback path and the transport does not bypass or downgrade the gate
