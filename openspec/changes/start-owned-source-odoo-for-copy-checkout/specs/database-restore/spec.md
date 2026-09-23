## ADDED Requirements

### Requirement: COPY checkout owns a bounded source Database Manager lifecycle

COPY environment checkout SHALL make its source Database Manager available through the existing project runtime and source configuration before the first COPY preflight, backup, or restore request. It SHALL reuse a responsive Database Manager without claiming or stopping its process. When the endpoint is unavailable, it SHALL start a bounded auxiliary Odoo process only after proving the configured local port free, track the exact process handle it created, wait for `/web/database/list` readiness, and stop and unregister only that owned process. An occupied endpoint that is not responsive and whose compatible runtime ownership cannot be proven SHALL fail closed without signalling, replacing, or restarting the listener. The auxiliary start, readiness, and cleanup actions SHALL be part of the same immutable checkout command and sanitized dry-run plan. Cleanup SHALL run after success, failure, readiness timeout, early exit, backup or restore failure, and cancellation; it SHALL remove owned temporary secret configuration and SHALL preserve the primary operation error when cleanup also fails. Existing COPY journal, target-absence, backup provenance, restore postcondition, and compensating-cleanup guarantees SHALL remain unchanged, and shared database checkout SHALL not acquire this lifecycle.

#### Scenario: Stopped source starts and is cleaned after success

- **WHEN** COPY checkout finds the source Database Manager unavailable and its configured local port free
- **THEN** it starts the configured project Odoo runtime, waits for Database Manager readiness, performs the existing guarded copy, and stops and unregisters the exact process it started

#### Scenario: Responsive external Database Manager is reused

- **WHEN** the configured source Database Manager already returns a valid database-list response
- **THEN** COPY checkout performs the existing guarded copy without spawning, registering, signalling, or stopping that process

#### Scenario: Compatible recorded runtime is reused

- **WHEN** the endpoint is not yet responsive but the occupied configured port is proven to belong to a compatible recorded project or environment runtime
- **THEN** COPY checkout waits for and reuses that runtime without claiming or stopping it

#### Scenario: Unknown occupied listener fails closed

- **WHEN** the configured port is occupied, the Database Manager is unusable, and compatible runtime ownership cannot be proven
- **THEN** checkout fails before owned checkout artifacts or database mutations and sends no signal to the listener

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
