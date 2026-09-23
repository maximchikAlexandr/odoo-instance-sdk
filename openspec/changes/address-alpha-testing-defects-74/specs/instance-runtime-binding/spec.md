## MODIFIED Requirements

### Requirement: Persisted environment runtime can be stopped safely

`OdooInstance` foreground runtime identity registration and cleanup SHALL remain under the artifact lock, but the wait for the foreground process SHALL happen outside the lock so a parallel `stop` can acquire the exclusive lock, read the persisted runtime identity, and call the existing `terminate_pid()`. Expression SHALL NOT appear in lock acquire/release, registration, wait, or cleanup. PID/create-time/process-group validation and stale-runtime safety SHALL be preserved because they operate on the persisted identity, not on the lock holder.

#### Scenario: foreground wait does not block stop

- **WHEN** a foreground `run` is waiting on the foreground process
- **THEN** the artifact lock is not held and a parallel `stop` acquires the exclusive lock and terminates the registered runtime

#### Scenario: identity validation is preserved

- **WHEN** a parallel `stop` reads the persisted runtime identity
- **THEN** PID/create-time/process-group validation runs as before and stale-runtime recognition is preserved