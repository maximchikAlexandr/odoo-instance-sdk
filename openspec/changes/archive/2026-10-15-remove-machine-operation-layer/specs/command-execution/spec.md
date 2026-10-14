## REMOVED Requirements

### Requirement: Same-snapshot preview approval and execution
**Reason**: The bounded machine approval session is removed because no approving machine consumer exists.
**Migration**: Human CLI confirmation and direct SDK `Command.plan`/`run()` usage retain their existing contracts.

### Requirement: Bounded decision and cancellation lifecycle
**Reason**: JSONL approval decisions, timeouts, and session cancellation are removed with the session transport.
**Migration**: Existing command execution, interruption, rollback, process identity, and cleanup requirements remain authoritative.
