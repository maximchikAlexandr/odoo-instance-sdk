## Context

See proposal.md. The monitor uses a persisted `(pid, create_time)` tuple to
avoid treating a reused PID as its former Odoo process. The historical optional
dependency design could not produce that tuple with the same clock primitive as
the monitor.

## Goals / Non-Goals

**Goals:**

- Have exactly one exact process-identity primitive for write and read paths.
- Preserve `.pgpass` authentication while excluding ambient psql/libpq startup
  configuration from SDK subprocesses.
- Reap an owned foreground process if waiting fails unexpectedly, without
  allowing best-effort cleanup failure to mask the original wait or persistence
  error. Persistence failure is fail-closed: the newly spawned owned group is
  terminated before that original error is re-raised.

**Non-Goals:**

- Changing public monitor snapshot fields or the dashboard dependency boundary.
- Migrating or rewriting archived OpenSpec artifacts.

## Decisions

- Make `psutil>=5.9,<7` a core dependency. Exact `Process.create_time()` is
  required for PID-reuse safety; `time.time()` is a different clock and cannot
  be reconciled exactly. Keeping an optional fallback would preserve a known
  false-stopped state. Alternatives considered: reject foreground tracking when
  psutil is absent (less usable) and record an approximate marker (weaker and
  adds a second identity contract).
- Use the same `psutil.Process(pid).create_time()` value when persisting and
  collecting a runtime. This makes the equality comparison meaningful.
- Treat an exception from the foreground wait as an abnormal owned-process
  exit: terminate/reap its process group, clear the catalog best-effort, then
  re-raise. Normal exit and Ctrl+C retain their existing lifecycle paths.
- Invoke psql with `-X` and remove `PSQLRC`, `PGSERVICE`, `PGSERVICEFILE`, and
  `PGOPTIONS` from its environment. Do not set `PGPASSFILE`: omitting
  `PGPASSWORD` deliberately preserves libpq's normal `.pgpass` lookup required
  by the existing restore-tracking contract.
- Read monitor environments and their current runtimes through one catalog
  method that opens one transactionally consistent SQLite read snapshot and
  performs two SELECTs there, eliminating mixed observations without claiming
  a nonexistent single aggregate query.

## Risks / Trade-offs

- [A new core wheel dependency] → `psutil` has maintained wheels for supported
  platforms; lockfile and package tests verify resolution.
- [Unexpected wait failures may occur during process teardown] → cleanup is
  limited to the process group owned by this call and is regression-tested.
- [Ambient psql configuration could be legitimately desired] → explicit SDK
  connection parameters are authoritative; `.pgpass` remains available.

## Migration Plan

1. Install a release containing the updated core metadata; ordinary upgrades
   install `psutil` automatically.
2. Remove the obsolete `metrics` extra from local install commands.
3. Rollback is a package-version rollback; catalog identity rows already use
   the exact create-time schema and need no data migration.
