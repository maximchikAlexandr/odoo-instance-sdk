## Context

The restore pipeline already holds the project preparation lock, restores into a managed filestore, can reset the administrator, and switches the project manifest plus generated config atomically. Its failure path records an exact `incomplete` restore binding when an existence probe proves the selected target. That binding currently proves ownership and source provenance, but it does not prove that import/verification/filestore work completed; the only supported retry therefore reconciles the target and imports again.

The public `db restore` leaf, module update operation, administrator-reset operation, typed command planning, stage observer, catalogue migrations, and `switch_project_default()` are existing extension points. The design must preserve the previous default until completion, fail closed for foreign or ambiguous targets, and keep all secrets out of durable evidence and output.

Ponytail full gate: no new subsystem is necessary. The required capability fits the existing catalogue migration, database-preparation command, project lock, module update, administrator reset, default-switch, CLI output, and test boundaries. A workflow engine, recovery daemon, second restore backend, parallel journal, generic stage framework, new public command leaf, and separate evidence runner would duplicate those alternatives, so all are removed. The retained additions are only one nullable closed checkpoint, exact compare-and-append transitions, one typed completion command, and the existing `db restore` mode needed to prevent destructive reimport; each closes a data-safety or public-contract gap that existing state cannot prove.

## Goals / Non-Goals

**Goals:**

- Prove whether the archive import and required restore postconditions finished before offering completion.
- Finish explicit module repairs, a pending administrator reset, and default publication against the same proven target without importing, dropping, or replacing it.
- Preserve inspect-first command planning, stable bounded output, ownership validation, locking, stale-binding protection, and idempotent retry behavior.
- Keep the ordinary restore path and destructive incomplete-import retry compatible.

**Non-Goals:**

- Resuming an interrupted or failed archive import.
- Automatically selecting modules, using `-u all`, changing module dependency semantics, or diagnosing the schema incompatibility.
- Adding a new top-level command, service, queue, background process, or supervisor integration.
- Making arbitrary PostgreSQL databases eligible through a database-name option alone.

## Decisions

### 1. Extend `db restore` with one mutually exclusive completion mode

The public syntax is `odcli db restore --complete DATABASE [--update-module MODULE ...] [--dry-run] [--yes]`. `--complete` is mutually exclusive with `BACKUP_UUID`, `--file`, `--target`, `--replace`, and `--reset-admin-password`. Repeating `--update-module` preserves explicit order after duplicate removal and is valid only with `--complete`; an empty module list means no compatibility update.

This keeps one `PUBLIC_LEAF_CASES` row and one output contract. A new `db complete-restore` command was rejected because it would add a second public leaf and duplicate confirmation, rendering, and provenance plumbing. Extending `module update` and `db reset-admin-password` with raw database selectors was rejected because those independent selectors would broaden mutation authority and still leave no safe public default-publication step.

### 2. Add one durable post-import checkpoint to append-only restore bindings

The catalogue gains nullable `post_restore_checkpoint` with the closed values `reset_pending`, `reset_complete`, and `reset_not_requested`. A null checkpoint means the binding does not prove a completed import and is not completion-eligible. The preparation pipeline sets a checkpoint only after database import, database verification, filestore restore, and neutralization return successfully:

- `reset_pending` when administrator reset was requested but has not succeeded;
- `reset_complete` after the requested reset succeeds;
- `reset_not_requested` when no reset was requested.

The pending step is derived: `admin_reset` for `reset_pending`, otherwise `default_switch`. Existing rows migrate with null and therefore remain eligible only for the existing guarded destructive retry. This is deliberately one checkpoint column, not a second event log. Separate booleans or a generalized stage table were rejected because they admit contradictory combinations and add unused workflow machinery.

When reset completes during a completion attempt, the catalogue appends a new exact incomplete binding with `reset_complete`; it does not emit another database-restored event. Successful default publication appends an exact `complete` binding with a null checkpoint through a dedicated compare-and-append catalogue operation, also without emitting a second restored event. Both operations validate that the latest row is the expected incomplete sequence and provenance, making a concurrent or stale plan fail closed.

### 3. Compose existing operations under the preparation lock

The public SDK adds a typed `complete_restore_command()` sibling on the existing environment/database-preparation resource. Planning resolves the project, latest exact binding, active cluster claim, endpoint, data directory, target existence/identity, current binding, requested modules, required pending step, and immutable child commands without mutation.

Execution reacquires the project preparation lock, revalidates all captured identities, then:

1. runs the existing module update command once for the explicit module tuple, if non-empty;
2. runs the existing administrator-reset command only for `reset_pending`, using the normal secret resolver and target-bound temporary config;
3. advances the append-only checkpoint after reset;
4. calls the existing `switch_project_default()` path and appends the complete binding.

It never opens the backup archive, invokes restore, drops the target, or modifies the previous default before the final switch. Existing module/process locking and the project preparation lock remain authoritative; no parallel runner is added.

### 4. Make failure and success output describe the same typed state

The typed completion result and failure context carry only secret-free target identity, completed stages, pending stage, module names requested, prior/effective default, checkpoint, and publication status. Rich, JSON, and TOON are projections of that object. A module or reset failure retains the latest checkpoint and prior default; a publication failure reports `default_switch` pending. Error text passes through existing sanitization.

If the latest binding is already complete and the project binding already selects the same target and managed filestore, completion returns an explicit no-op success without running modules or reset. A complete binding with a mismatched project default, or an incomplete binding changed since planning, is not treated as idempotent success and fails stale.

## Risks / Trade-offs

- **Legacy incomplete rows cannot establish import completion** → their null checkpoint is intentionally ineligible; users retain the current guarded reimport path.
- **A crash after administrator reset but before checkpoint append can repeat reset** → reset is already idempotent, while claiming an unrecorded checkpoint would be unsafe.
- **Explicit module updates can have application-specific side effects** → only user-named modules run, the plan exposes them, and failure never publishes the target.
- **Appending checkpoint rows increases restore-history rows** → completion adds at most one reset checkpoint and one terminal binding, avoiding an unbounded stage journal.
- **Project binding can change during repair** → capture and compare the binding under the existing preparation lock before publication; fail stale rather than overwrite newer state.

## Migration Plan

Add the nullable checkpoint column through the next Alembic catalogue revision with a closed-value check. Existing rows receive null. Release the migration, SDK command/result types, CLI extension, docs, and tests together. Rollback code may ignore the additive column; no target database or filestore data is migrated or deleted. A failed deployment leaves prior defaults and existing guarded restore retry behavior intact.

## Open Questions

None. The CLI syntax, checkpoint values, eligibility rule, operation order, idempotency contract, and migration behavior are fixed by this design.
