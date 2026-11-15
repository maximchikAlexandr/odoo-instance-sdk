## 1. Catalogue checkpoint contract

- [x] 1.1 Add the nullable closed post-restore checkpoint model and Alembic catalogue migration, preserving existing rows as null and extending migration/schema contract tests.
- [x] 1.2 Extend restore binding reads and writes with checkpoint projection plus compare-and-append operations for checkpoint advance and terminal completion that do not emit duplicate restored events.
- [x] 1.3 Add focused catalogue tests for valid transitions, stale/latest-binding refusal, legacy null ineligibility, source-neutral provenance, append-only history, and idempotent complete reads.

## 2. Preparation and SDK completion

- [x] 2.1 Record `reset_pending`, `reset_complete`, or `reset_not_requested` only after the shared import, verification, filestore, and neutralization boundary; project the completed and pending stages through typed failure context.
- [x] 2.2 Add typed completion plan/result models and the public inspectable `complete_restore_command()` boundary using the existing project preparation lock, target instance, module update, administrator reset, and default-switch operations.
- [x] 2.3 Implement execution-time revalidation for cluster, endpoint, database identity, data directory, source provenance, checkpoint, target existence, managed filestore, and captured project binding; reject null, foreign, malformed, stale, or concurrently changed evidence before mutation.
- [x] 2.4 Implement deterministic explicit module updates, checkpoint advancement, atomic default publication, and already-complete no-op behavior without archive access, restore, drop, target recreation, or duplicate restored events.
- [x] 2.5 Add focused unit and integration tests for import/reset/publication failures, module failure ordering, no-module completion, ownership and stale-plan refusal, lock serialization, crash-safe retry, idempotent success, and unchanged ordinary restore/reimport behavior.

## 3. Existing CLI leaf and documentation

- [x] 3.1 Extend `db restore` parsing/help with mutually exclusive `--complete DATABASE` and repeatable `--update-module MODULE`, preserving the single public-leaf inventory row and confirmation ordering.
- [x] 3.2 Project completion plans, successes, no-ops, interrupts, and failures through one sanitized Rich/JSON/TOON contract with exact target, checkpoint, completed/pending stages, requested modules, and prior/effective default.
- [x] 3.3 Add parametrized CLI tests for option exclusivity, dry-run inertness, explicit module order/deduplication, machine confirmation, format parity, exit codes, stage diagnostics, and secret/ANSI/prompt exclusion.
- [x] 3.4 Document the inspect-first completion sequence, legacy null-checkpoint limitation, explicit module selection, retry semantics, and unchanged destructive reimport path in the CLI and SDK references.

## 4. Real-boundary regression and delivery gates

- [x] 4.1 Extend the existing real-Odoo E2E tier with a focused scenario that injects an administrator-reset failure after successful import, completes the same target through the public CLI, proves data/filestore continuity and prior-default preservation, and verifies no second import or restored event.
- [x] 4.2 Add the focused real-Odoo node to the applicable targeted workflow disposition and preserve bounded secret-safe evidence, cleanup, and current CI timeout contracts without introducing a new E2E framework.
- [ ] 4.3 Run focused catalogue, preparation, SDK, CLI, documentation, migration, lint, type, and strict OpenSpec checks; use published exact-revision GitHub CI for any automatic suite predicted to exceed or reaching the ten-minute local limit.
