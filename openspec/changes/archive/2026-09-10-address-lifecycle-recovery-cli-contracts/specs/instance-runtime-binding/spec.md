## ADDED Requirements

### Requirement: Persisted environment runtime can be stopped safely

The existing runtime and environment records SHALL be the starting point, but not sole proof, for out-of-process stop. Without a runtime migration, the stop command SHALL re-read the runtime row's environment owner and PID/create time, and SHALL separately re-read the environment row's `runtime_json` for expected `odoo_bin`/`runtime_cwd` plus its generated-config path. Before signaling, it SHALL compare the live PID create time, executable, argv, cwd and config argument with those records. POSIX stop SHALL additionally require the live SDK-created process-group identity `pgid == pid` before using the existing bounded terminate-and-kill escalation; Windows SHALL require the same available identity checks before existing process-tree termination. Successful exit SHALL be verified before the matching runtime row is cleared. The implementation SHALL add no runtime field, migration, second process registry or supervisor.

#### Scenario: Persisted identity matches

- **WHEN** the re-read runtime owner/PID/create time, environment runtime/config expectations and every required live-process identity check match at execution time
- **THEN** the existing process boundary terminates only that process tree, verifies absence, and clears only that environment's runtime row

#### Scenario: PID was reused

- **WHEN** the PID exists but its create time, executable, argv, cwd, config argument or required POSIX process group differs, or any required identity evidence is inaccessible
- **THEN** no signal is sent and the stale row is retained for actionable diagnosis

#### Scenario: Process exited before execution

- **WHEN** planning observed a matching process but execution revalidation proves that PID absent
- **THEN** stop returns idempotent success and clears the now-stale matching runtime row without signaling another process
