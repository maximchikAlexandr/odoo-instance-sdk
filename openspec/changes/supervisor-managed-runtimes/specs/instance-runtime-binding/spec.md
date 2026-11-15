# Spec Delta

## MODIFIED Requirements

### Requirement: Project runtime cleanup preserves stale-process safety

Project-owned runtime identity SHALL be cleared when the Supervisor program has exited. A foreground client that detaches with Ctrl-C SHALL leave the runtime row in place. Readers SHALL validate the persisted program and launch identity before treating either owner kind as live.

#### Scenario: Project runtime exits

- **WHEN** a project-owned program exits normally or fails
- **THEN** its runtime identity is cleared without deleting project registration

#### Scenario: Project foreground detach keeps the row

- **WHEN** the user detaches from a project-owned foreground run with Ctrl-C
- **THEN** the runtime row remains and project registration is unchanged

### Requirement: Persisted environment runtime can be stopped safely

The owner-neutral runtime record and its captured launch identity SHALL be the starting point for out-of-process stop. The runtime catalogue SHALL keep one nullable JSON field for a versioned launch-identity document. Every new environment- or project-owned foreground or detached launch SHALL populate that field from the exact immutable program command used by execution, before log following or detached return. The document SHALL contain only JSON-safe, secret-free identity evidence: schema version, canonical executable, the common redacted argv projection with preserved boundaries, captured sensitive-argument positions, executable-prefix length, canonical cwd, and canonical config path. It SHALL NOT contain raw passwords, secret-bearing values, inherited environment values, or a digest of secret material.

On Unix, when that owner has a Supervisor program, stop SHALL address only that program through `supervisorctl stop` after the program command matches the captured launch identity. It SHALL NOT signal an arbitrary PID, SHALL NOT run `stop all`, and SHALL NOT reload the daemon. A repeated stop of an already stopped program SHALL succeed. A program command that does not match the captured identity SHALL fail closed without stopping it. A runtime row written before Supervisor, with no program configuration, SHALL keep the existing identity-checked signal path: re-read PID and create time, compare executable, captured argv, cwd, and config, and on POSIX require `pgid == pid` before the bounded terminate-and-kill escalation. Windows managed stop SHALL fail with the unsupported-Supervisor error when a Supervisor program is required, and SHALL keep the legacy path only for a pre-Supervisor row.

The stop path SHALL NOT rebuild authoritative launch identity from current project manifest, current `StartConfig`, current default run args, generated configuration contents, Git branch, or checkout revision. Mismatch evidence SHALL be bounded, deterministic, and value-free. An argv mismatch SHALL identify `executable-prefix` or the differing protected option name, such as `--database`. A secret-bearing option SHALL retain its option name and redaction marker only. A malformed or absent launch-identity document on a legacy row SHALL fail closed before signaling and retain the row.

Successful stop SHALL be verified before an atomic conditional delete of the exact owner row. Project registration SHALL remain intact. `OdooInstance` runtime identity registration SHALL remain under the artifact lock, but log following SHALL happen outside the lock so parallel stop remains possible. Expression SHALL NOT appear in lock acquire/release, registration, follow, or cleanup.

#### Scenario: Environment-owned captured identity matches

- **WHEN** an environment-owned Supervisor program matches the identity captured from its immutable launch command
- **THEN** stop stops only that program, verifies it is stopped, and clears only that environment-owned runtime row

#### Scenario: Project checkout evolves after launch

- **WHEN** a project-owned program remains unchanged while the checkout branch, revision, manifest-derived defaults, or current configuration source changes after launch
- **THEN** stop validates against the persisted launch identity, stops the owned program, clears only its project runtime row, and preserves project registration

#### Scenario: Protected live binding differs

- **WHEN** the program command differs from the captured launch identity in an executable-prefix element or a non-secret protected binding such as `--database`, `--config`, `--addons-path`, or `--http-port`
- **THEN** the program is not stopped, the runtime row is retained, and the error names only `argv: executable-prefix` or the differing option name without either value

#### Scenario: Secret-bearing identity remains redacted

- **WHEN** launch argv contains a secret-bearing protected option
- **THEN** the persisted document, plan, fingerprint, representation, and mismatch diagnostic use the common redaction marker and never contain or digest the raw secret

#### Scenario: PID was reused or process identity changed

- **WHEN** a legacy pre-Supervisor PID exists but its create time, executable, captured argv evidence, cwd, config argument, or required POSIX process group differs
- **THEN** no signal is sent and the stale row is retained for actionable diagnosis

#### Scenario: Legacy runtime lacks captured identity

- **WHEN** a migrated pre-change runtime row has no captured launch-identity document and no Supervisor program
- **THEN** stop fails closed with a sanitized `captured launch identity unavailable` reason, sends no signal, retains the row, and does not reconstruct authority from mutable current configuration

#### Scenario: Process exited before execution

- **WHEN** planning observed a matching persisted owner but execution finds the program or legacy PID already gone
- **THEN** stop returns idempotent success and conditionally clears only the now-stale matching owner row without signaling another process

#### Scenario: Runtime row changed before cleanup

- **WHEN** the matching owner's PID, create time, or captured launch identity changes after planning but before execution or cleanup
- **THEN** stop fails closed without stopping or deleting the replacement runtime row

#### Scenario: Foreground wait does not block stop

- **WHEN** a foreground `run` is following the program log
- **THEN** the artifact lock is not held and a parallel stop acquires the exclusive lock and stops the program

#### Scenario: Repeated stop is safe

- **WHEN** stop is invoked for a program that is already stopped
- **THEN** the command succeeds and does not affect any other program
