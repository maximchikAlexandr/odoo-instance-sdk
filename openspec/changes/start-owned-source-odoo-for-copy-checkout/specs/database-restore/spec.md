## ADDED Requirements

### Requirement: COPY checkout owns a bounded source Database Manager lifecycle

COPY environment checkout SHALL make its source Database Manager available through the existing project runtime and source configuration before the first COPY preflight, backup, or restore request. It SHALL reuse a runtime without claiming or stopping it only when a persisted runtime record, exact live PID/create-time/process identity, configured endpoint, and live listening socket owned by that exact PID all match. A responsive but unrecorded listener and every occupied listener whose exact process-to-socket ownership cannot be proven SHALL fail closed without an HTTP trust probe, secret transmission, signal, replacement, or restart. When the configured port is proven free, checkout SHALL start a bounded auxiliary Odoo process, track the exact process handle it created, wait for `/web/database/list` readiness, and stop and unregister only that owned process. Immediately before every auxiliary Database Manager request carrying `master_pwd`, the active session SHALL revalidate the exact process-to-socket proof for that request's source instance and SHALL not create or send the privileged HTTP request when proof fails. Recorded identity, port/ownership, privileged-request revalidation, auxiliary start, readiness, and cleanup SHALL be honest actions in the same immutable checkout command and sanitized dry-run plan. Cleanup SHALL run after success, failure, readiness timeout, early exit, backup or restore failure, and cancellation; it SHALL remove owned temporary secret configuration and SHALL preserve the primary operation error when cleanup also fails. Existing COPY journal, target-absence, backup provenance, restore postcondition, and compensating-cleanup guarantees SHALL remain unchanged, and shared database checkout SHALL not acquire this lifecycle.

#### Scenario: Stopped source starts and is cleaned after success

- **WHEN** COPY checkout finds the source Database Manager unavailable and its configured local port free
- **THEN** it starts the configured project Odoo runtime, waits for Database Manager readiness, performs the existing guarded copy, and stops and unregisters the exact process it started

#### Scenario: Responsive unrecorded listener fails closed

- **WHEN** an unrecorded process occupies the configured source port and returns a valid database-list response
- **THEN** COPY checkout rejects the listener before checkout mutation without probing it for trust, spawning, registering, signalling, stopping it, or sending the master password

#### Scenario: Recorded runtime with exact socket ownership is reused

- **WHEN** the persisted project or environment runtime record, PID, create time, process identity, configured endpoint, and live listening socket owner all match
- **THEN** COPY checkout waits for bounded readiness and reuses that runtime without claiming, registering, signalling, or stopping it

#### Scenario: Recorded process without socket ownership fails closed

- **WHEN** a compatible recorded process is live but the configured listening socket is absent, belongs to another PID, is ambiguous, or cannot be inspected
- **THEN** checkout fails before owned checkout artifacts or database mutations and sends no HTTP request, secret, or signal to the listener

#### Scenario: Every privileged auxiliary request revalidates endpoint identity

- **WHEN** recorded or owned runtime identity was previously accepted but exact process-to-socket proof no longer holds immediately before a COPY source backup or standalone auxiliary restore request
- **THEN** the operation fails closed before invoking privileged HTTP and does not send `master_pwd`

#### Scenario: Plan exposes ownership and request authorization

- **WHEN** COPY checkout command public and private plans are inspected
- **THEN** both contain the same ordered recorded-identity, port/ownership, privileged-request revalidation, start, readiness, and cleanup actions around their exact runtime effects

#### Scenario: Readiness failure cleans owned startup state

- **WHEN** the auxiliary process exits early or Database Manager readiness times out
- **THEN** checkout unregisters and terminates only its exact owned process, removes its temporary secret configuration, and returns the readiness failure

#### Scenario: Copy failure or cancellation cleans the auxiliary process

- **WHEN** COPY preflight, backup, restore, postcondition verification, compensation, or the surrounding operation fails or is cancelled after auxiliary startup
- **THEN** the existing COPY recovery contract runs and the exact owned auxiliary process and secret configuration are cleaned without replacing the primary failure with a cleanup failure

#### Scenario: Dry-run exposes the possible lifecycle without spawning

- **WHEN** COPY checkout is inspected through `--dry-run`
- **THEN** its single sanitized immutable plan includes auxiliary start, readiness, and cleanup actions and performs no spawn, registration, signal, secret-file write, checkout mutation, backup, or restore

#### Scenario: Shared checkout remains unchanged

- **WHEN** environment checkout uses shared database mode
- **THEN** its plan and execution contain no auxiliary source Database Manager lifecycle and retain their existing behavior
