## Context

The checkout command declares one immutable `database.restore.exists-before` step and one `database.restore.exists-after` step for COPY mode. `_preflight_copy_checkout()` already queries the source/local instance before any owned artifact is created and rejects an existing target with `DatabaseAlreadyExistsError`.

During execution, `_prepare_copy_database()` performs another `instance.databases.exists(target_db)` only for named-remote and selected-backup inputs. Because `DatabaseResource.exists()` detects the active `RunContext`, it binds that nested query to the first planned unconsumed existence step: `database.restore.exists-before`. The coordinator then calls `_consume_copy_database_probe()` with the same step ID, and immutable-ledger enforcement raises a duplicate-consumption error before restore. Local-source COPY avoids that exact extra call and already uses the coordinator-owned before/after pair.

The correction must preserve the repository's single-snapshot execution architecture, public typed error behavior, source-neutral backup validation, and COPY recovery semantics. No new execution step or parallel context is needed.

## Goals / Non-Goals

**Goals:**

- Make selected-backup and named-remote COPY execution consume each planned restore existence step exactly once.
- Preserve the artifact-free checkout preflight that rejects an existing target before mutation.
- Preserve the coordinator-owned execution-time absence check immediately before restore, plus the post-restore existence check.
- Prove public dry-run/execution parity and both successful and existing-target outcomes through real command execution with a recording/fake executor.

**Non-Goals:**

- Adding a third target-existence step, changing step identifiers, or weakening `RunContext` duplicate-consumption enforcement.
- Refactoring `DatabaseResource.exists()`, the restore transport, source acquisition, journal schema, provenance, rollback, locking, redaction, or owned-artifact cleanup.
- Adding a CLI-only branch, a catalog bypass, a live-service dependency, or a new public SDK type.

## Decisions

### Remove the redundant explicit-source execution check

`_prepare_copy_database()` SHALL not call `instance.databases.exists(target_db)` between backup acquisition/journal preparation and `_consume_copy_database_probe(..., "database.restore.exists-before", ...)`. The earlier `_preflight_copy_checkout()` query remains the early rejection point, and the explicit coordinator probe remains the authoritative execution-time race-closing check.

This is preferred over assigning the redundant query a new step ID because a third probe would expand the immutable plan without a distinct safety responsibility. It is preferred over changing nested `DatabaseResource.exists()` selection because that shared behavior supports other coordinators and the defect is a duplicate caller in this path.

### Keep one command snapshot for preview and execution

The existing before/after `PreparedStep` pair in `checkout_artifacts.py` remains unchanged. Dry-run SHALL expose that same pair, and execution SHALL consume those exact IDs in order around `_restore_after_verified_absence()`. No step is optionalized, cloned, or recomputed for explicit-source inputs.

This retains the public plan contract and lets the regression assert parity without introducing source-specific planning branches.

### Verify through the public command boundary

Focused tests SHALL build a retained catalog backup, create the public checkout command, inspect its dry-run/public plan, and execute it with the existing recording/fake process boundary while exercising a real `DatabaseResource` rather than a mocked `databases.exists()` method. Parameterized coverage SHALL include selected-backup and named-remote explicit sources where their shared path can be exercised without live services.

The success case SHALL record every restore probe consumption and prove one before plus one after; the existing-target case SHALL prove `DatabaseAlreadyExistsError` occurs in preflight and restore is never invoked. Existing focused unit coverage for journal/provenance/cleanup remains the regression guard for unchanged behavior.

## Risks / Trade-offs

- **[Risk] Removing a second check could appear to widen a race window.** → `_preflight_copy_checkout()` still rejects known existing targets before mutation, while the coordinator-owned planned before probe remains immediately before restore and is the authoritative race-closing check.
- **[Risk] Mock-heavy tests can hide active-context step aliasing.** → The new regression uses the public command with the real database resource and a recording/fake executor, and asserts consumed step IDs rather than only method calls.
- **[Risk] Named-remote setup may require more acquisition fixtures than selected backup.** → Parameterize the shared execution contract where existing fixtures permit; otherwise add a focused named-remote regression that proves it reaches the same coordinator-owned pair without adding a separate implementation path.

## Migration Plan

No data or configuration migration is required. Release the implementation and tests as a backward-compatible bug fix. Rollback is the single implementation commit; catalog rows, backup files, and public plans require no conversion.

## Open Questions

None. The existing preflight and coordinator-owned probe responsibilities determine the implementation boundary.
