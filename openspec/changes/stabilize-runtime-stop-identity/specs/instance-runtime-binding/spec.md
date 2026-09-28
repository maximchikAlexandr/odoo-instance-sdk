## MODIFIED Requirements

### Requirement: Persisted environment runtime can be stopped safely

The owner-neutral runtime record and its captured launch identity SHALL be the starting point for out-of-process stop. The runtime catalogue SHALL add one nullable JSON field for a versioned launch-identity document. Every new environment- or project-owned foreground or detached launch SHALL populate that field from the exact immutable `PreparedStep` used by execution, before foreground waiting or detached return. The document SHALL contain only JSON-safe, secret-free identity evidence: schema version, canonical executable, the common redacted argv projection with preserved boundaries, captured sensitive-argument positions, executable-prefix length, canonical cwd, and canonical config path. It SHALL NOT contain raw passwords, secret-bearing values, inherited environment values, or a digest of secret material.

The stop path SHALL select the exact `_RuntimeBinding` owner, re-read that owner's runtime row and PID/create time, decode the captured launch identity, and project the live argv through the same common redaction rules and persisted sensitive positions. It SHALL compare PID create time, executable, captured executable prefix, protected option names and projected values, cwd, and config path. POSIX stop SHALL additionally require the live SDK-created process-group identity `pgid == pid` before using the existing bounded terminate-and-kill escalation; Windows SHALL require the same available identity checks before existing process-tree termination. The stop path SHALL NOT rebuild authoritative launch identity from current project manifest, current `StartConfig`, current default run args, generated configuration contents, Git branch, or checkout revision.

Mismatch evidence SHALL be bounded, deterministic, and value-free. An argv mismatch SHALL identify `executable-prefix` or the differing protected option name, such as `--database`; other mismatch labels SHALL remain limited to safe component names such as `create_time`, `executable`, `cwd`, `config`, and `process_group`. A secret-bearing option SHALL retain its option name and redaction marker only; neither persisted state nor exceptions SHALL expose its value. A malformed, unsupported, or absent launch-identity document SHALL fail closed before signaling and retain the runtime row with an actionable sanitized reason.

Successful exit SHALL be verified before an atomic conditional delete of the exact `(owner_kind, owner_id, root_pid, create_time)` row. Project registration SHALL remain intact. The implementation SHALL add no second process registry, supervisor, port-based ownership inference, or owner-specific termination algorithm. `OdooInstance` foreground runtime identity registration and cleanup SHALL remain under the artifact lock, but foreground waiting SHALL happen outside the lock so parallel stop remains possible. Expression SHALL NOT appear in lock acquire/release, registration, wait, or cleanup.

#### Scenario: Environment-owned captured identity matches
- **WHEN** an environment-owned runtime row contains the identity captured from its immutable launch step and every required live-process identity check matches that snapshot
- **THEN** the existing process boundary terminates only that process tree, verifies absence, and clears only that environment-owned runtime row

#### Scenario: Project checkout evolves after launch
- **WHEN** a project-owned runtime remains unchanged while the checkout branch, revision, manifest-derived defaults, or current configuration source changes after launch
- **THEN** stop validates against the persisted launch identity, terminates the owned process, verifies exit, clears only its project runtime row, and preserves project registration

#### Scenario: Protected live binding differs
- **WHEN** the live process differs from the captured launch identity in an executable-prefix element or a non-secret protected binding such as `--database`, `--config`, `--addons-path`, or `--http-port`
- **THEN** no signal is sent, the runtime row is retained, and the error names only `argv: executable-prefix` or the differing option name without either value

#### Scenario: Secret-bearing identity remains redacted
- **WHEN** launch argv contains a secret-bearing protected option
- **THEN** the persisted document, plan, fingerprint, representation, and mismatch diagnostic use the common redaction marker and never contain or digest the raw secret

#### Scenario: PID was reused or process identity changed
- **WHEN** the PID exists but its create time, executable, captured argv evidence, cwd, config argument, or required POSIX process group differs, or required identity evidence is inaccessible
- **THEN** no signal is sent and the stale row is retained for actionable diagnosis

#### Scenario: Legacy runtime lacks captured identity
- **WHEN** a migrated pre-change runtime row has no captured launch-identity document
- **THEN** stop fails closed with a sanitized `captured launch identity unavailable` reason, sends no signal, retains the row, and does not reconstruct authority from mutable current configuration

#### Scenario: Process exited before execution
- **WHEN** planning observed a matching persisted owner but execution revalidation proves that PID absent for either owner kind
- **THEN** stop returns idempotent success and conditionally clears only the now-stale matching owner row without signaling another process

#### Scenario: Runtime row changed before cleanup
- **WHEN** the matching owner's PID, create time, or captured launch identity changes after planning but before execution or cleanup
- **THEN** stop fails closed without signaling or deleting the replacement runtime row

#### Scenario: Foreground wait does not block stop
- **WHEN** a foreground `run` is waiting on the foreground process
- **THEN** the artifact lock is not held and a parallel stop acquires the exclusive lock and validates the captured identity before termination

