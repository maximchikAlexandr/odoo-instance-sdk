## MODIFIED Requirements

### Requirement: `copy` DB mode

`copy` mode SHALL:

1. Probe the configured local source Odoo Database Manager before creating a catalog row, worktree, generated config, Python environment, backup, or target database.
2. If the probe succeeds, use that Database Manager without starting or claiming its process.
3. If the probe raises `DatabaseManagerUnavailableError`, construct a bounded auxiliary source Odoo from the captured project runtime and the exact selected source configuration, require the configured HTTP endpoint to be free or owned by a live recorded runtime for the same project, wait for Database Manager readiness, and retry the probe exactly once.
4. Keep an auxiliary process started by this checkout alive through the source backup and target restore, then stop and unregister that exact owned process and remove its temporary secret config on success, failure, or interruption. A reused runtime SHALL NOT be stopped or unregistered.
5. Create a separate ZIP backup of the source DB with filestore through the existing `backup()` pipeline.
6. Save `backup_id` as owned by this environment.
7. Restore the target DB in the same cluster through the existing restore pipeline with `copy=True` and `neutralize_database=True`.
8. Verify the postcondition `exists(target_db) is True`.
9. Only after the postcondition succeeds, transition the environment to `ready`.

The auxiliary start, readiness, and cleanup steps SHALL be captured in the same immutable `checkout_command()` ledger as the Git, Python, backup, restore, and cleanup steps. Command construction and dry-run SHALL remain non-mutating and SHALL expose only redacted public projections. A foreign or stale listener, unsafe or incomplete project runtime, startup/readiness failure, failed retry, or inability to clean up an owned helper SHALL fail closed with actionable diagnostics. The implementation SHALL preserve target non-overwrite, COPY journal, backup ownership, provenance, rollback, and retained-evidence behavior and SHALL NOT add a second backup or restore implementation.

#### Scenario: Copy checkout with available source

- **WHEN** `checkout --db-mode copy --source-db comerta --target-db comerta_x` probes a responsive configured source Database Manager
- **THEN** checkout uses that endpoint without starting or stopping a process, creates the owned backup, restores `comerta_x`, verifies it exists, and records the environment as `ready`

#### Scenario: Stopped source starts an owned helper

- **WHEN** COPY checkout receives `DatabaseManagerUnavailableError` from the configured local source, the project runtime is complete, and the configured HTTP endpoint is free
- **THEN** checkout starts one captured auxiliary source Odoo, waits for Database Manager readiness, retries the source probe once, completes the existing backup and restore pipelines, and cleans up that owned helper

#### Scenario: Recorded project runtime becomes available

- **WHEN** the initial source probe is unavailable but the configured endpoint is owned by a live recorded runtime for the same project
- **THEN** checkout reuses the recorded runtime, retries the source probe once, and neither stops nor unregisters that runtime

#### Scenario: Foreign listener blocks fallback

- **WHEN** the source probe is unavailable and the configured endpoint is occupied without live same-project ownership evidence
- **THEN** checkout fails before durable checkout artifacts or database mutation and neither uses nor stops the listener

#### Scenario: Auxiliary source cannot become ready

- **WHEN** the owned helper cannot start, cannot pass Database Manager readiness, or the one retry remains unavailable
- **THEN** checkout fails before durable checkout artifacts or database mutation, removes its temporary secret material, and terminates and unregisters only a helper it started

#### Scenario: Failure after auxiliary source startup

- **WHEN** backup, restore, postcondition, or later checkout work fails after the helper was started
- **THEN** the existing COPY journal and rollback rules apply and helper cleanup still runs without stopping any unrelated process

#### Scenario: Copy checkout dry-run

- **WHEN** a caller inspects or previews a COPY `checkout_command()` for a source that may require fallback
- **THEN** the redacted plan includes the conditional auxiliary start, readiness, and cleanup operations and performs no HTTP retry, process start, catalog write, filesystem mutation, backup, or restore

#### Scenario: Remote source Odoo refused

- **WHEN** `checkout --db-mode copy` selects a non-local source Odoo endpoint
- **THEN** checkout fails the local-only guard without starting an auxiliary process or creating durable checkout artifacts
