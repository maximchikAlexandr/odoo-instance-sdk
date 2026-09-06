## 1. P0 shell transaction correctness

- [x] 1.1 Extend the nonce-framed shell payload and CLI failure projection with sanitized `transaction`, `user_error`, and `finalization_error` fields while preserving existing successful result/stdout framing.
- [x] 1.2 Rewrite the shared shell wrapper state machine so successful code commits only for `commit=True`, successful non-commit code rolls back, and failed code always attempts rollback without ever committing.
- [x] 1.3 Classify successful-body commit/rollback failures as transaction-finalization failures and user-body-plus-rollback failures as user-code failures with distinct finalization detail; ensure both exit non-zero and produce `ok=false`.
- [x] 1.4 Add wrapper unit tests for conditional write then user exception, commit exception, rollback exception after user failure, rollback exception after successful non-commit code, and unchanged successful commit/rollback behavior.
- [x] 1.5 Add shared-consumer contract tests proving eval, exec, module update/test, translations export, administrator reset, and database preparation all retain the same wrapper and sanitized failure mapping.
- [x] 1.6 Commit the transaction slice with a Conventional Commit message and push the feature branch before starting the next slice.

## 2. P0 dependency verification and test isolation

- [x] 2.1 Correct `verify_deps_command()` capture so an explicit Python path uses the configured `uv_executable` with `uv pip check --python <path>`, while uv selectors use a valid selector-aware uv form and import probes keep the selected interpreter.
- [x] 2.2 Define one dependency-verification success predicate (`pip_check_ok` and no missing imports) and use it for envelope `ok`, Rich status, JSON/TOON parity, and exit 0/1 without installing packages.
- [x] 2.3 Add command/CLI tests for explicit venv paths, configured uv executable, uv selectors, distribution conflict only, missing import only, combined failure, complete success, dry-run argv, and diagnostic redaction.
- [x] 2.4 Add one shared pytest fixture that patches the `get_catalog_path()` symbol imported by the CLI and supplies controlled worker-local XDG/data roots and sentinel catalogue paths for every init CLI test, without adding a production setting or storage abstraction.
- [x] 2.5 Add a parallel-suite regression that spies on path resolution and file open/read/write calls, fails on any access to the production-resolved catalogue path without opening or snapshotting it, and proves monitor fixtures cannot see another worker's projects.
- [x] 2.6 Commit the dependency/test-isolation slice and push the feature branch before starting the next slice.

## 3. P0 process streaming and timeout diagnostics

- [x] 3.1 Consolidate ordinary observed/timeout capture and `run_captured_limited()` onto one `internal/proc` Popen pump that concurrently writes stdin and drains stdout/stderr, preserves `ProcessResult`, and leaves inherited/native execution behavior unchanged; retain immediate limited-output termination at the configured byte limit.
- [x] 3.2 Implement a per-stream incremental decoder/redactor that preserves every canonical structural detector in `internal/proc/redaction.py` (password/token assignments, Bearer/Basic credentials, Authorization/Proxy-Authorization/Cookie/Set-Cookie headers, URI userinfo, and JWTs) plus configured/captured runtime secrets; retain sufficient prefix/value candidate state so no emitted event sequence can reconstruct raw values, and resolve/redact retained candidates on success, failure, timeout, and Ctrl-C before safe flush.
- [x] 3.3 Extend typed timeout failure data with actual elapsed time, bounded newest stdout/stderr tails, and independent truncation flags; route every public representation through existing redaction and sanitization.
- [x] 3.4 Write the exact immutable `PreparedStep.stdin` bytes without observer/output exposure, close stdin after completion, and make input writing concurrent with both drains so large stdin cannot deadlock behind child output.
- [x] 3.5 Ensure early child exit, timeout, Ctrl-C, and stdin write failure close stdin and output pipes, terminate/reap the exact owned process group, and leave no writer/reader activity alive without replacing existing return/exception semantics.
- [x] 3.6 Add deterministic fake-process tests proving output arrives before completion, stdout/stderr ordering within each stream, final capture parity, observer-exception isolation, no output observation when disabled, and exact large-stdin delivery while the child concurrently emits output.
- [x] 3.7 Add a table-driven compatibility gate for every canonical structural detector and configured/captured runtime secrets at all significant prefix/value split points, including byte and multibyte decoder boundaries, observer-event reconstruction checks, success/failure/timeout/Ctrl-C final flush, and unchanged final captured projection; retain timeout-tail, truncation, large-stdin early-exit/timeout, and process-group cleanup coverage on POSIX and supported Windows paths.
- [x] 3.8 Add an architecture regression proving `run_captured_limited()` delegates to the common pump, preserves immediate byte-limit stop, and `internal/proc` contains only one pipe/timeout/cleanup algorithm with no command-local subprocess seam or executor.
- [x] 3.9 Commit the process-boundary slice and push the feature branch before starting the next slice.

## 4. P0 logical progress and Rich completion

- [x] 4.1 Add explicit action progress/completion APIs to `RunContext` and additive typed event fields for elapsed time and reliable units/total; make callback failure close only still-started actions as failed.
- [x] 4.2 Update current action-backed commands so each logical completion is emitted immediately after its effect and postcondition rather than at overall callback return.
- [x] 4.3 Replace the restore-only renderer with one concrete bounded Rich runner in the existing output module: TTY live status, deterministic sparse non-TTY lines, elapsed time, byte totals when reliable, and no generic renderer registry/DSL.
- [x] 4.4 Wire the shared observer to `env checkout`, `env sync`, `test`, `module test`, `module update`, `exec`, `eval`, `translations export`, `db refresh --restore`, `postgres up`, and `postgres approve-image`; document measured exclusions for fast read-only leaves.
- [x] 4.5 Ensure every long-command dry-run renders only its immutable plan and every JSON/TOON execution emits one progress-free document with diagnostics confined to stderr.
- [x] 4.6 Add the common final Rich success projection from `OutputDocument` with `status=success`, applicable `database|url|backup|modules`, and `transaction=commit|rollback` for exec, without changing JSON/TOON or native commands.
- [x] 4.7 Add controlled slow-step tests for pre-completion visibility, TTY cleanup, non-TTY determinism, unknown-total spinner/status, reliable-total percentage, action postcondition failure, Ctrl-C exit 130, and one final completion line.
- [x] 4.8 Commit the progress/output slice and push the feature branch before starting resource lifecycle work.

## 5. Streaming backup and interruption lifecycle

- [x] 5.1 Change remote backup acquisition to `httpx.Client.stream("POST", ...)`, validate status/headers before transfer, and preserve password-safe HTTP exception conversion without retaining request graphs.
- [x] 5.2 Extend the existing exclusive `.part` writer to emit received-byte progress while incrementally enforcing the size limit and SHA-256, then fsync/close and atomically publish only a complete stream.
- [x] 5.3 Parse only trustworthy byte `Content-Length` values, verify final count equality, and suppress percentage for missing, invalid, encoded, or inconsistent totals.
- [x] 5.4 Separate remote-response waiting and transfer action events and propagate backup UUID/state through typed failure and interrupt context.
- [x] 5.5 Make interruption close response/file handles and apply existing cleanup/failed policy while preserving any already-published backup and avoiding claims about remote cancellation.
- [x] 5.6 Add tests for no full response buffering, chunked success, checksum/count, response wait, absent/valid/invalid/inconsistent content length, stream break, over-limit stop, atomic publication, cleanup, and interrupt before/during/after publication.
- [x] 5.7 Commit the backup-streaming slice and push the feature branch before adding point-management commands.

## 6. Backup catalogue point operations

- [x] 6.1 Add CLI-private/internal typed exact-UUID lookup and state-aware backup projections that keep catalogue state, actual file presence, recorded bytes, occupied bytes, history, restore links, and environment links distinct without changing the public method inventory.
- [x] 6.2 Add deterministic source/database/all-state list queries with validated limit and opaque `(catalogue-time, UUID)` keyset cursor executed in one read transaction.
- [x] 6.3 Reuse the current operation-lock implementation with a backup-UUID key across restore/validation/delete and refuse delete for downloading or actively locked backups.
- [x] 6.4 Add CLI-private/internal exact UUID resolution that captures identity/relationships, then reuses the existing public `BackupResource.validate_command` and `delete_command` methods; re-read state, path, regular-file identity, containment, and symlink status under lock before effect without adding public method siblings.
- [x] 6.5 Make deletion unlink only the exact selected file, verify absence before `record_deletion`, preserve UUID/history/restore links, return explicit already-deleted/missing-file results, and leave audit unchanged on filesystem failure.
- [x] 6.6 Add catalogue/resource tests for malformed/unknown UUID, filters, sort ties, cursor pages, all states, missing available file, busy restore/download, stale target, symlink escape, permission failure, idempotent retry, and retained relationships.
- [x] 6.7 Apply only additive backup-query catalogue fields/indexes needed by this slice transactionally with pre-change fixtures and rollback tests; leave cluster ownership provenance to the ordered substrate in section 8 and do not add a second lifecycle/event store.
- [x] 6.8 Add a canonical public-API regression gate proving `test_discovered_public_methods` and the public `DatabaseResource`, `BackupResource`, and cluster/resource method inventory remain unchanged.
- [x] 6.9 Commit the backup-resource slice and push the feature branch before CLI registration.

## 7. Backup CLI surface

- [x] 7.1 Register `backup list` with source/database/all/limit/cursor options and Rich table plus JSON/TOON projections from the same typed state-aware result.
- [x] 7.2 Register exact-UUID `backup show` and `backup validate`, including history/relationship detail and the invalid-versus-validator-unavailable distinction without requiring Odoo/worktree context.
- [x] 7.3 Register `backup delete UUID --dry-run --yes` with immutable preview, Rich confirmation, noninteractive machine confirmation failure, exact result/partial diagnostics, and no arbitrary path or abbreviated ID acceptance.
- [x] 7.4 Add all four backup leaves exactly once to `PUBLIC_LEAF_CASES` and add CLI tests for context independence, full UUID display, filters/pages, all formats, confirmation ordering, dry-run non-mutation, exit codes, and terminal/secret sanitization.
- [x] 7.5 Commit the backup-CLI slice and push the feature branch before local restore work.

## 8. Cluster ownership substrate and registered local database restore

- [x] 8.1 Add a transactional catalogue migration for an internal state-constrained `postgres_clusters` claim (`pending|active`) and nullable `cluster_id` plus restore-time data-directory identity on restores/database events; preserve legacy rows as readable identity-null/unknown and add no public SDK method, second catalogue, or event store.
- [x] 8.2 Implement the project-lock handshake: atomically create/reuse `pending` before Compose effects, render its ID into named-volume and service/container labels, and promote the same row to `active` only after readiness and exact attachment/label inspection; never let pending authorize restore/drop or infer/backfill from declarative mode or a lone volume.
- [x] 8.3 Make creation retry reuse pending identity when no volume exists or an exact matching labeled volume exists; fail closed without relabel/adopt/delete/new UUID for missing, malformed, foreign, or mismatched evidence.
- [x] 8.4 Extend the common completed-restore audit transaction with nullable ownership provenance: write the exact non-null `cluster_id` only for a verified active managed target, write null/unknown for external and no-claim legacy Compose targets, and refuse pending or malformed/mismatched existing claims; adopt this contract first in existing remote `db refresh --restore`, then expose the same helper to the catalogue source.
- [x] 8.5 Add migration/handshake tests for legacy null rows and interrupt/failure before volume, after volume/up, and before activation commit, plus successful retries for no-volume and exact-matching-volume pending states and fail-closed mismatches.
- [x] 8.6 Commit the ownership substrate and remote-restore adoption with a Conventional Commit and push the feature branch before starting local restore implementation.
- [x] 8.7 Introduce the closed internal remote-versus-catalogue backup source input and refactor preparation so both sources share target reservation, validation, restore, neutralization, optional admin reset, nullable identity-aware postcondition/audit, failure retention, and default switch.
- [x] 8.8 Add the catalogue-source inspectable command that locks project preparation and backup UUID, performs exact state/file/checksum/format/project-binding/ownership-classification/target/capability preflight, permits external or no-claim legacy targets, refuses pending or inconsistent claims, and consumes no remote/download action.
- [x] 8.9 Preserve existing generated safe target naming when omitted, reject an existing or unsafe exact `--target`, and allow one UUID to produce multiple restore audits for distinct targets.
- [x] 8.10 Switch the project default atomically only after restore, neutralization, postcondition, nullable identity-aware audit, and optional administrator reset all succeed; retain the backup and any confirmed database on later failure.
- [x] 8.11 Map Ctrl-C to exit 130 after closing progress/locks/handles and include exact backup UUID, target database, confirmed database state, and confirmed default-switch state in sanitized failure context.
- [x] 8.12 Register `db restore UUID --target --reset-admin-password --dry-run --yes`, with Rich confirmation/progress, noninteractive machine confirmation rules, single-document formats, and immutable preview.
- [x] 8.13 Add SDK/CLI tests for remote restore on external and no-claim legacy targets, catalogue restore on both target classes with no remote call, active Compose restore with the exact non-null identity, nullable audit rows, pending refusal, malformed/mismatched-claim refusal, every existing preflight refusal, default/exact target, repeated restore, optional admin reset, each failure boundary, default-switch atomicity, retained artifacts, interrupt before download-equivalent stage/after archive/during restore, and JSON/TOON/Rich parity.
- [x] 8.14 Add `db restore` once to `PUBLIC_LEAF_CASES`; run restore integration tests against disposable external, no-claim legacy Compose, and verified active-identity targets; assert a completed null/unknown-identity restore subsequently receives a zero-session/drop/audit/filestore-effect `db drop` refusal; then commit and push the local-restore slice.

## 9. Database inventory and filestore-aware drop

- [x] 9.1 Add a CLI-private/internal typed read-only project-cluster database inventory through the existing PostgreSQL transport, returning exact cluster/name, logical size, sessions, default, environment/runtime bindings, and restore/backup provenance or unknown without adding a public SDK method.
- [x] 9.2 Join catalogue/runtime relationships without calling side-effecting Odoo `DatabaseResource.list()`/`exists()` reconciliation, and add `--tracked` filtering over proven identities only.
- [x] 9.3 Register `db list --tracked` in all bounded formats and add it exactly once to `PUBLIC_LEAF_CASES` as read-only.
- [x] 9.4 Make drop planning and immediate pre-mutation revalidation consume the established evidence by comparing the inspected current volume label/identity, exact active project claim, and latest completed exact `(cluster_id, database)` restore binding; reject pending/missing/malformed/mismatched identity, endpoint reuse, and stale evidence before termination, drop, audit, or filestore mutation.
- [x] 9.5 After verified PostgreSQL deletion/audit only, evaluate filestore cleanup for an already ownership-authorized database: remove only the exact proven contained non-symlink filestore, preserve unknown filestore ownership, and return a typed non-zero partial result when filesystem cleanup fails after database success.
- [x] 9.6 Add read-only inventory tests for stopped Odoo, restricted dbfilter, unavailable/partial PostgreSQL, duplicate names across clusters, unknown origin, deterministic ordering, and zero catalogue writes.
- [x] 9.7 Add ownership-consumer tests for pending claim refusal, declarative Compose without active evidence, legacy and newly recorded external/no-claim null restore identities, endpoint reuse by another `cluster_id`, missing/changed/malformed evidence, mismatched exact restore, and allowed disposable exact active match; assert every refusal has zero session/drop/audit/filestore effects.
- [x] 9.8 Add remaining drop tests for active environment/process refusal, proven/unknown/symlinked filestore, partial filesystem failure, retry/audit truth, preserved source backup, confirmation, and dry-run.
- [x] 9.9 Commit the database-lifecycle slice and push the feature branch before resource overview work.

## 10. Resource inventory and doctor

- [ ] 10.1 Define CLI-private/internal frozen typed resource inventory/finding models with stable identity, type, relationships, ownership confidence, active use, logical/measured byte semantics, completeness, reclaimability, sanitized local path, and recommendation.
- [ ] 10.2 Implement one read-only resource command by joining the existing backup catalogue, database inventory, project/environment records, restore provenance, storage-footprint helpers, log locations, and Compose volume probes without new persistence.
- [ ] 10.3 De-duplicate shared Python environments and volumes by stable identity, keep project-context resources without synthetic environments, and retain monitor/API path redaction unchanged.
- [ ] 10.4 Extend owned Compose volume inspection with redacted stable identity, available usage, and retained/non-reclaimable state; prove `postgres stop` never deletes the volume or reports reclaimed bytes.
- [ ] 10.5 Implement doctor findings for available-record/missing-file mismatches, crash-left `.part`, unknown owned-directory files, unknown/preserved filestores, cleanup-failed environments, and unavailable measurements without audit reconciliation or deletion.
- [ ] 10.6 Register read-only `resource list` and `resource doctor` Rich/JSON/TOON leaves and add each exactly once to `PUBLIC_LEAF_CASES`.
- [ ] 10.7 Add tests for relationship graphs, repeated restore, shared-resource de-duplication, project-only context, logical-versus-host bytes, stopped/external/unknown volumes, handled failure without `.part`, crash leftovers, recommendations, sanitization, partial probes, and zero mutations.
- [ ] 10.8 Document why prune, log rotation, and `postgres destroy` remain separate evidence-gated future changes, then commit and push the resource slice.

## 11. Acceptance and delivery

- [ ] 11.1 Update CLI/SDK documentation, examples, security notes, failure/interrupt semantics, migration notes, and changelog for transaction finalization, progress/streaming, backup commands, local restore, database inventory/drop, and resource diagnosis.
- [ ] 11.2 Run strict OpenSpec validation, Ruff, formatting check, strict mypy, architecture inventory, full parallel Python tests, package build/install smoke, and any frontend/OpenAPI checks affected by shared models.
- [ ] 11.3 Run real streaming, interrupt, restore, database-drop, filestore, and resource acceptance cases only on newly created disposable files/catalogues/processes/clusters and record sanitized evidence plus cleanup outcomes.
- [ ] 11.4 Run the full parallel suite with a deny-access spy for the production-resolved catalogue path, verify all catalogue access stays in controlled worker-local roots without reading the production path, and confirm no disposable database, process, `.part`, filestore, worktree, volume, or lock remains.
- [ ] 11.5 Confirm every implementation commit is present on `origin/feat/MYL-110-execution-resource-lifecycle`, open a PR referencing GitHub #55, and map each included acceptance criterion to tests while explicitly listing prune/log rotation/cluster destroy as deferred follow-up scope.
