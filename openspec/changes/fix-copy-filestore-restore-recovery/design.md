## Context

COPY checkout currently derives `data_dir` before the final checkout/artifact paths are known. Ordinary checkout can therefore bind restore provenance to the primary project while the generated environment and verifier resolve another checkout-local path. `_do_copy_restore()` leaves `environment_copy_journal.stage=restore_pending` until attachment verification finishes, even though `DatabaseResource._restore_after_verified_absence()` has already recorded a complete exact restore. A verifier failure then blocks the existing guarded removal path.

The repository already has the required seams:

- `_CheckoutPlan`, `generate_config()`, and `_copy_auxiliary_session()` capture environment runtime identity.
- `capture_selected_backup_restore()` and `materialize_selected_backup_filestore()` validate and stream ZIP filestore members without following archive paths.
- `verify_copy_filestore()` validates database attachment paths with descriptor-relative no-follow traversal.
- complete restore provenance and `environment_copy_journal` provide durable database/backup/cluster evidence.
- `_preflight_copy_remove()` and `_drop_copy_target()` implement exact, retryable, public cleanup.

GitHub #200 / PR #203 concerns main-project post-restore completion and does not replace this environment COPY lifecycle. If it lands first, this change must preserve its restore checkpoint behavior while keeping environment journal ownership separate.

## Goals / Non-Goals

**Goals:**

- Make one immutable checkout-local `data_dir` authoritative across config, restore provenance, verification, and cleanup.
- Materialize the validated source baseline into only the exact target filestore.
- Distinguish a source-baseline missing blob from target-only loss.
- Make a complete import with a later postcondition failure removable through existing public `env rm`, including after catalog reopen.
- Preserve no-follow path, exact ownership, borrowed-backup, and unrelated-resource protections.

**Non-Goals:**

- No new CLI command, general recovery workflow, restore backend, daemon, or queue.
- No repair or fabrication of blobs missing from the source baseline.
- No relaxation for incomplete, foreign, identity-null, or mismatched restores.
- No changes to main-project default switching or PR #203 post-restore completion semantics.

## Decisions

### D1. Capture the target data directory after the checkout path is allocated

For COPY mode, planning will derive the target data directory as the checkout-local managed path `<checkout>/.odcli/filestore` after `worktree` is fixed. The plan will validate lexical containment before mutation and revalidate canonical containment and every existing path component with no-follow semantics after the checkout exists. Ordinary checkout and caller-owned adoption use the same rule; SHARED checkout retains its current binding behavior.

The generated environment config, local auxiliary restore runtime, restore runtime binding, restore provenance, attachment verifier, and cleanup plan will all consume `plan.data_dir` without re-deriving it from the primary project or source config. This uses the path the task Odoo process actually reads and avoids a second identity field.

Alternative rejected: keep the primary project data directory and teach the verifier to look there. That would preserve shared filestore coupling, contradict isolated COPY ownership, and leave adopted checkouts inconsistent.

### D2. Reuse the validated selected-backup payload as the source baseline

After backup acquisition and before database restore, COPY execution will capture the exact selected ZIP through the existing source-neutral payload validation. It will create only the contained `<data_dir>/filestore/<target_database>` destination and materialize validated archive members through the existing bounded streaming helper. The source archive and source filestore remain unchanged. DUMP-only input remains invalid for COPY because it cannot prove the requested filestore baseline.

The execution snapshot will retain the normalized set of archive-relative filestore members for post-restore comparison. It will not add another extractor, checksum format, or persistent source inventory table.

Alternative rejected: rely solely on Odoo's HTTP extractor. That cannot independently prove which source members existed when classifying a later missing target file and reproduces the identity mismatch at issue.

### D3. Classify attachment postconditions against the captured baseline

The existing bounded target attachment query remains the public-boundary observation. Each normalized safe `store_fname` is classified as follows:

- present as a regular contained target file: pass;
- absent/non-regular in the target but present in the validated archive member set: target loss, fail closed;
- absent in both the target and validated archive member set: source-baseline defect, emit stable warning code `copy_source_blob_missing` and continue;
- unsafe, undecodable, symlinked, or over-limit input: fail closed regardless of baseline membership.

The successful result will expose warnings through the existing `DevelopmentEnvironment.warnings` surface, and the successful checkout event will retain a sanitized stable warning summary so a catalog reopen does not turn the finding into an integrity claim. No blob is synthesized.

Alternative rejected: ignore every missing target file that is absent from the archive. The explicit warning is required to avoid claiming clean source data.

### D4. Let `restored` mean proven import ownership, not readiness

After the restore call, target-existence/OID probe, and exact complete restore-provenance write succeed, execution will re-read and match the restore row against the journal's target database, backup, endpoint, cluster identity, and authoritative `data_directory`. Only then will it promote `restore_pending` to `restored`, before attachment verification. Environment `ready` remains later and still requires all filestore/postconditions.

If the exact completed row is absent, incomplete, foreign, or mismatched, the journal remains `restore_pending` and cleanup continues to fail closed. A postcondition failure after promotion may be compensated immediately; if compensation cannot complete, repeated public `env rm` reuses the existing `restored` preflight and direct guarded drop after catalog reopen. Repeated removal remains idempotent, borrowed backups remain retained, and only the exact target database/filestore may be deleted.

Alternative rejected: add an `imported` journal stage. Existing `restored` already gates destructive ownership and adding a stage would require schema migration plus duplicate transition logic without a distinct safety benefit.

### D5. Keep verification ownership in existing test layers

Focused unit tests will cover plan identity, materialization/classification, journal promotion, ownership mismatches, ordinary/adopted checkout, catalog reopen, and idempotent remove. The existing real-Odoo path will be extended only enough to prove that a real restored attachment is read from the isolated target and that post-restore removal does not touch the source or unrelated databases/filestores.

## Risks / Trade-offs

- **Existing caller-owned checkout contains `.odcli/filestore` data** -> Planning requires the exact target database directory to be absent and cleanup deletes only the proven target child, never the checkout or sibling filestores.
- **Odoo restore and explicit materialization both touch the target root** -> Materialization happens from one verified snapshot before restore, and post-restore comparison detects target divergence; tests cover existing-directory behavior with the supported Odoo version.
- **A complete restore row could be stale or belong to another attempt** -> Promotion matches exact target OID, backup, endpoint, cluster, and data-directory evidence produced by the current restore before changing the journal.
- **Warnings could disappear from the returned catalog model** -> Finalization carries the current warning tuple into the typed result and writes a sanitized stable summary to the existing lifecycle event.
- **PR #203 changes restore evidence first** -> Implementation rebases onto the approved integration base and maps the same exact completed-restore facts without sharing its main-project completion command.

## Migration Plan

No catalog schema migration is required. New checkouts use the checkout-local identity immediately. Existing `restore_pending` rows are never promoted by shape alone: public removal may reconcile one only when current database inspection plus an exact complete restore row match all journal/config/cluster/data-directory evidence. Otherwise behavior remains fail-closed. Rollback is a code rollback; it does not rewrite catalog rows or delete retained resources.

## Open Questions

None. The implementation SHALL follow the decisions above without selecting an alternate path or recovery surface.
