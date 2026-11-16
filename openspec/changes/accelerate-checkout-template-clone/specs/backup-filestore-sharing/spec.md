# Spec Delta

## Purpose

Record the fail-closed decision for backup-scoped shared filestore so isolated restore behavior stays the only production mode until the research gate is proven.

## ADDED Requirements

### Requirement: Shared filestore research fails closed

The product SHALL NOT expose a production shared filestore mode in this change. Backup restore and backup COPY checkout SHALL continue to materialize an isolated filestore. A template clone SHALL NOT join a backup-scoped pool. Requests for a shared filestore mode SHALL be rejected. No filestore daemon, scheduler, or automatic garbage-collection job SHALL be added.

#### Scenario: Isolated restore stays isolated

- **WHEN** a database is restored from a backup without a new filestore mode
- **THEN** its filestore is a private directory and no shared pool, membership row, or garbage-collection command is created

#### Scenario: Shared mode is rejected

- **WHEN** a caller requests shared filestore for checkout or database refresh
- **THEN** the command fails before restore or checkout mutation and leaves existing isolated databases unchanged

### Requirement: Research gate is an explicit failure

The shared-filestore research gate SHALL be recorded as failed for this change. The failure reason SHALL state that the gate needs automated proof, on Linux and macOS, that two databases restored from one verified backup can share an append-only pool without missing or corrupted attachments, that only Odoo filestore garbage collection is suppressed, and that membership and rollback survive restart. Until that proof exists, phase-2 pool storage, `filestore status`, and `filestore gc` SHALL NOT be implemented.

#### Scenario: Failed gate ships no pool

- **WHEN** the recorded research decision is fail
- **THEN** the catalogue, CLI, and SDK have no backup filestore pool and isolated filestore behavior is unchanged
