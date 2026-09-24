## MODIFIED Requirements

### Requirement: Persisted environment runtime can be stopped safely

The existing owner-neutral runtime record and canonical owner inputs SHALL be the starting point, but not sole proof, for out-of-process stop. Without a runtime migration, the stop path SHALL select the exact `_RuntimeBinding` owner (`environment | project`), re-read that owner's runtime row and PID/create time, and derive expected executable, argv, cwd, and config from canonical inputs for the same resolved owner. Environment expectations SHALL continue to come from its recorded runtime/config artifacts; project expectations SHALL come from the initialized project's resolved instance inputs used by detached launch. Before signaling, the stop path SHALL compare the live PID create time, executable, argv, cwd, and config argument with those expectations. POSIX stop SHALL additionally require the live SDK-created process-group identity `pgid == pid` before using the existing bounded terminate-and-kill escalation; Windows SHALL require the same available identity checks before existing process-tree termination. Successful exit SHALL be verified before an atomic conditional delete of the exact `(owner_kind, owner_id, root_pid, create_time)` row. Project registration SHALL remain intact. The implementation SHALL add no runtime field, migration, second process registry, supervisor, or owner-specific termination algorithm.

#### Scenario: Environment-owned persisted identity matches

- **WHEN** the selected environment owner/PID/create time, canonical environment runtime/config expectations, and every required live-process identity check match at execution time
- **THEN** the existing process boundary terminates only that process tree, verifies absence, and clears only that environment-owned runtime row

#### Scenario: Project-owned persisted identity matches

- **WHEN** the selected project owner/PID/create time, canonical project runtime/config expectations, and every required live-process identity check match at execution time
- **THEN** the same process boundary terminates only that process tree, verifies absence, clears only that project-owned runtime row, and preserves project registration

#### Scenario: PID was reused

- **WHEN** the PID exists but its create time, executable, argv, cwd, config argument, or required POSIX process group differs, or any required identity evidence is inaccessible
- **THEN** no signal is sent and the stale row is retained for actionable diagnosis

#### Scenario: Process exited before execution

- **WHEN** planning observed a matching process but execution revalidation proves that PID absent for either owner kind
- **THEN** stop returns idempotent success and conditionally clears the now-stale matching owner row without signaling another process

#### Scenario: Runtime row changed before cleanup

- **WHEN** the matching owner's PID or create time changes after validation but before cleanup
- **THEN** conditional cleanup fails without deleting the replacement runtime row

