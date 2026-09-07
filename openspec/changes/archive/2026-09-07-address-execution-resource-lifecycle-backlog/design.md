## Context

`main` at `0aeaf0c` already provides immutable `Command`/`ExecutionPlan` snapshots, a single `internal/proc` execution boundary, `StepObserver`, one bounded `OutputDocument` pipeline, project-context runtime resolution, a SQLite backup/environment/runtime catalogue, restore preparation, guarded direct-PostgreSQL `db drop`, and monitor/storage collectors. Inside that boundary, ordinary execution uses `subprocess.run`, while `run_captured_limited()` already owns a separate `Popen`/selector drain, timeout, byte-limit, and cleanup path. GitHub #55 identifies correctness gaps inside those foundations rather than missing replacement architectures.

The shared Odoo wrapper in `internal/server.py` currently finalizes the transaction in `finally`, commits even after user-code failure when `commit=True`, and suppresses commit/rollback exceptions. `verify_deps_command()` constructs an invalid explicit-interpreter pip-check argv and the CLI only fails for missing imports. Ordinary `SubprocessExecutor` capture uses `subprocess.run`, so observed output is emitted only after completion; `_BufferedStepObserver` protects split secrets by buffering the entire stream, and timeout errors discard child output. Meanwhile `run_captured_limited()` duplicates pipe draining and timeout/cleanup around `Popen`. Logical action completion is delayed until the whole command callback returns, which is insufficient evidence that an individual effect finished.

Backup writing already uses an exclusive `.part`, incremental bytes and SHA-256, atomic rename, catalogue states, and cleanup, but `http.post()` buffers the response before `iter_bytes()`. `BackupResource` already has list/history/validate/delete operations over `Backup` values, and database preparation already owns remote download, restore, neutralization, optional admin reset, audit, failure retention, and default switching. The change therefore extends those paths with exact UUID resolution and a local-backup source rather than introducing another catalogue or restore engine.

## Goals / Non-Goals

**Goals:**

- Make transaction, dependency verification, process observation, timeout, progress, and interrupt results truthful in every output mode.
- Provide safe, inspectable point operations for catalogue backups and project-cluster databases.
- Reuse the existing preparation, drop, monitor, output, and catalogue primitives while making ownership and partial outcomes explicit.
- Preserve machine-output determinism, secret redaction, immutable preview parity, and audit history.
- Deliver in ordered vertical slices, with P0 correctness complete before resource lifecycle expansion.

**Non-Goals:**

- A second executor, resource catalogue, restore pipeline, event store, output-document type, renderer hierarchy, or background worker.
- Rollback of explicit commits made inside user code or of non-transactional external effects.
- Backup resume/range protocols, arbitrary-path restore/import, implicit replacement of an existing database, or automatic deletion of a prior database/backup.
- Automatic garbage collection, age-based pruning, log rotation, or `postgres destroy`; those require a separate evidence-backed change after this one.
- Treating `postgres stop` as storage reclamation or treating logical PostgreSQL size as host-reclaimable bytes.

## Decisions

### 1. Model shell completion as a three-phase outcome inside the existing frame

The generated shell wrapper will run user code, then choose exactly one finalization action: commit only after successful user code when requested, otherwise rollback. It will capture `user_error`, `finalization_error`, and `transaction=commit|rollback` in the existing nonce-bound payload. A user-code failure remains the primary classification when rollback also fails, with the rollback failure attached as finalization detail; a successful user body followed by commit or rollback failure is a transaction-finalization failure. In both cases the process exits non-zero and the CLI emits `ok=false` after applying existing sanitization.

This keeps every current caller—eval/exec, module operations/tests, translations, admin reset, and preparation—on one wrapper. A helper-specific transaction wrapper was rejected because it would immediately diverge across callers.

### 2. Make `internal/proc` the only streaming and timeout authority

One common `Popen`-based pump inside `internal/proc` will own stdin writing, stdout/stderr draining, timeout, byte-limit termination, and cleanup. It will serve ordinary observed/timeout capture and the existing `run_captured_limited()` API; the latter delegates to the pump while preserving immediate termination as soon as either stream would exceed its byte limit. There SHALL be no second pipe/timeout/cleanup algorithm in `internal/proc`, and no caller will spawn, select, or read pipes itself.

The pump receives the immutable `PreparedStep.stdin` snapshot and writes those exact bytes without decoding, normalization, logging, observer delivery, or inclusion in output. Stdin writing and both output drains proceed concurrently so a child that produces output before consuming a large input cannot deadlock. The pump closes child stdin after the snapshot is fully written, and on early child exit, timeout, Ctrl-C, or write failure it closes stdin, drains or closes the output pipes as appropriate, terminates and reaps the exact owned process group, and preserves the established result/exception semantics.

The current whole-stream `_BufferedStepObserver` will become a stateful per-stream incremental decoder/redactor that preserves the complete canonical semantics of `internal/proc/redaction.py`: password/token-style assignments, Bearer and Basic credentials, Authorization/Proxy-Authorization/Cookie/Set-Cookie headers, URI userinfo, JWTs, and configured/captured runtime secret values. Per stream, it retains the earliest suffix that can still begin or continue any canonical structural candidate, carries detector state across byte, decoder, and observer boundaries, and withholds a candidate's raw value until a delimiter proves it safe or the detector replaces it. Thus no sequence of emitted observer events can reconstruct a raw credential. Success, process failure, timeout, and Ctrl-C all resolve or redact incomplete candidates before a safe final flush. The final captured `ProcessResult` continues through the current whole-value canonical projection unchanged. Observer chunks are diagnostics and go to stderr with `step_id`; observer exceptions remain non-semantic.

Alternative considered: tee raw pipes in individual CLI commands. Rejected because it duplicates process ownership, timeout, redaction, and final capture.

### 3. Add explicit logical-step completion and one concrete bounded progress renderer

`RunContext.action(step_id)` will still consume and start a planned action, but effect code will explicitly mark that action completed immediately after its postcondition. Command-level failure will close any still-started action as failed. Process completion remains executor-owned. `StepEvent` gains additive elapsed/progress fields sufficient for byte transfers; a progress event never proves completion.

`commands/output.py` will provide one concrete Rich bounded-command runner used by the listed long operations. TTY mode may use `Live`/spinner; redirected Rich writes sparse deterministic started/completed/failed lines with elapsed time. A percentage is rendered only when the event carries a trustworthy total, such as a validated HTTP `Content-Length`; otherwise it shows status or received bytes. JSON/TOON never attach an observer. Dry-run renders the immutable plan only.

Fast read-only leaves remain outside this renderer after a focused duration/behavior inventory. This avoids a generic renderer framework while preventing command-local copies of the current restore renderer.

### 4. Fix dependency verification at command capture

`verify_deps_command()` will capture `uv_executable pip check --python <explicit-path>` for filesystem interpreters. Existing uv selectors continue through the selector-aware uv prefix, using a valid uv pip-check form. Import probes continue under the selected interpreter. `DepsVerifyResult` remains the one result but gains/uses a single success predicate: `pip_check_ok` and no `missing_imports`. The CLI builds the envelope and exit code from that predicate and exposes all non-empty distribution diagnostics and missing module/import pairs without installing anything.

### 5. Stream backup HTTP bodies into the existing atomic lifecycle

`DatabaseResource._download_backup_part()` will use `httpx.Client.stream("POST", ...)`. After headers and status validation, it will pass the live response to the existing exclusive `.part` writer, which continues to count bytes, update SHA-256, enforce the size ceiling before writing an over-limit chunk, fsync/close, and publish by atomic rename only after success. A trustworthy non-negative `Content-Length` supplies a progress total and is rejected if the final count disagrees; absent, encoded, or otherwise unreliable lengths never produce a percentage.

Waiting for response headers and transferring bytes are distinct logical events. HTTP exceptions are still converted without retaining password-bearing request graphs. Existing failed/download cleanup policy remains authoritative; interruption closes response/file handles and records the known catalogue state without claiming the remote Odoo stopped generating the archive.

### 6. Protect exact backup point operations with existing catalogue state plus per-backup locks

CLI-private/internal catalogue helpers will resolve an exact UUID to the existing `Backup` value and state-aware projection. The CLI will then reuse the existing public `BackupResource.validate_command` and `delete_command` surfaces with that exact resolved value; it will not add UUID-based public method siblings. List queries will return stable `(downloaded_at desc, UUID asc)` ordering with validated limit/cursor pagination and optional inclusion of non-available states. Catalogue state and current file presence are separate typed fields.

Restore and delete will acquire the existing operation-lock mechanism under a backup-UUID key. Downloading rows are not deletable; an available backup locked by restore is busy. Delete acquires the lock, re-reads the row, verifies the captured identity and content path, rejects symlinks or containment changes, unlinks only that file, verifies absence, and then records `deleted`. Missing files and already-deleted rows return explicit idempotent results; filesystem failure leaves audit state unchanged. Restore history is never removed.

Alternative considered: add a persistent lease/event subsystem. Rejected because the existing cross-process lock and catalogue states cover the required exclusion without a second lifecycle store.

### 7. Establish cluster identity before either restore path

The minimal ownership source of truth is an additive `postgres_clusters` row in the existing SQLite backup catalogue, not `PostgresCluster.owned`. Under the existing project-cluster lock and before the first Compose side effect, cluster creation transactionally inserts one non-authoritative `pending` claim containing a random immutable `cluster_id`, exact project id, Compose project name, and expected named data-volume identity. Compose rendering and creation use that persisted ID as the SDK ownership label on the named data volume and attached service/container. Only after readiness plus inspection proves the exact current target is attached to the expected volume carrying the pending ID does one catalogue transaction promote that same row to authoritative `active`. Only `active` permits non-null ownership provenance and can later authorize drop; an external or no-claim legacy target may record completed nullable provenance but that provenance can never authorize drop. A `pending` or malformed/mismatched existing claim blocks restore.

Retry reads rather than replaces the claim. A pending claim with no volume resumes creation using its existing ID; a pending claim with the exact matching labeled volume repeats readiness/attachment inspection and may promote to active. Missing, malformed, foreign, or mismatched volume evidence fails closed without relabeling, adoption, deletion, or a new UUID. A lost catalogue row is never reconstructed from a volume label alone. Interrupt/failure before volume creation, after volume/up, or before the activation commit therefore remains recoverable through the same pending ID while never granting destructive authority.

The additive migration creates the state-constrained ownership row and nullable cluster identity/data-directory columns for restore provenance before local restore work. The common preparation/audit path classifies the current target before either restore source mutates it. A verified `active` managed Compose claim must match current attachment/label inspection and causes the exact non-null `cluster_id` to be stored atomically on both the completed restore row and restored database event. A `pending` claim denotes incomplete managed creation and blocks both restore sources until activation; malformed or mismatched evidence for an existing claim also fails closed. By contrast, an external PostgreSQL binding or legacy Compose target with no authoritative claim remains a supported restore target and records `cluster_id=null`/unknown. That nullable provenance is valid history but can never become ownership evidence or authorize `db drop`, even if endpoint coordinates later match. The existing remote `db refresh --restore` adopts this nullable audit contract first, and the catalogue source uses the same preparation/audit path. No claim or restore identity is backfilled from manifest mode, endpoint fields, or a lone volume label, and no public SDK method, second catalogue, or second event store is introduced.

### 8. Parameterize the existing preparation workflow by backup source

Database preparation will accept an internal closed source union: remote source (current refresh behavior) or catalogue UUID (new local restore). Both converge before validation and use the same target-name reservation, restore call, neutralization, postcondition, nullable identity-aware audit, optional admin reset, failure context, and atomic project-default switch. Target classification is independent of source: external and no-claim legacy Compose bindings are compatible and record unknown ownership, verified active managed Compose records its exact identity, and pending managed creation refuses before mutation. Local restore performs no remote Odoo request and no download; it validates available state, exact identity, readable file, checksum/format, project PostgreSQL binding, and a collision-free target before mutation.

The default target is generated by the existing safe naming rules and reserved under the project preparation lock. `--target` must be an exact valid new name. One UUID may be restored repeatedly to different names. Confirmation is a CLI concern: Rich prompts, machine formats require `--yes`, and dry-run never prompts or mutates.

On Ctrl-C, the coordinator maps to exit 130, closes progress, and emits only known retained backup UUID, target database, and whether the default switch was confirmed. It never deletes an available backup or a confirmed database merely because the local client was interrupted.

### 9. Inventory databases through PostgreSQL without catalogue reconciliation side effects

`db list` will use the existing bound PostgreSQL transport and maintenance database to query exact database names, sizes, and active-session counts. It will left-join catalogue restore provenance and environment/project runtime bindings in memory. `--tracked` filters to databases with proven SDK relationships. Unknown origin remains unknown. The read path never calls `DatabaseResource.list()`/`exists()` because those methods currently reconcile missing Odoo-visible databases as dropped.

The existing guarded `db drop` builder consumes the already-established evidence and remains the sole direct-drop mechanism. Before session termination or any mutation, planning must inspect the current target volume label and require equality among its `cluster_id`, the exact `active` project catalogue claim, and the latest completed exact database restore binding. A `pending` claim never authorizes restore or drop. Planning also matches the recorded Compose project/volume identity to the current target. Unknown, missing, malformed, foreign, or mismatched evidence fails closed. The same evidence and active-binding/default/template/session safety values are re-read immediately before mutation; endpoint/port reuse, replaced volumes, or any stale/unavailable value aborts before session termination, `DROP DATABASE`, audit reconciliation, or filestore changes.

Only after database ownership passes may the builder capture a filestore disposition. After PostgreSQL absence is verified and audit reconciled, it removes `<recorded-data-dir>/filestore/<exact-database>` only when the restore binding also proves filestore ownership and containment without following a symlink. Unknown filestore ownership preserves the path with a warning, but can never relax the prior cluster/database ownership gate. A filestore failure after database success is a typed partial result and non-zero exit; the database is not recreated and the backup remains untouched.

### 10. Build resource inventory as a projection over existing sources

The CLI-private/internal `local-resource-lifecycle` projection will join the backup catalogue, database inventory, environment records, project manifests, restore provenance, current storage-footprint helpers, log locations, and owned Compose volume inspection through existing public SDK methods and internal catalogue/collector seams. It adds no public SDK resource surface. It may expose local paths because this is an explicit local maintenance command, but paths are sanitized and every filesystem traversal uses containment and no-follow rules. Shared venvs and volumes are de-duplicated by stable identity.

`resource list` reports logical size, measured host bytes where known, relationships, ownership confidence, active use, and measurement completeness. `resource doctor` reports catalogue/file mismatches, crash-left `.part` files, unknown files under SDK-owned directories, preserved unknown filestores, and `cleanup_failed` environments. Both are read-only and never convert observations into deleted audit state. Recommendations point to existing `env remove`, `backup delete`, or `db drop` commands.

### 11. Isolate init tests at the imported CLI catalogue seam

A session-independent pytest fixture will monkeypatch the symbol actually imported by `cli.py` and configure worker-local XDG/data roots plus a controlled sentinel catalogue for every init CLI test. Production configuration and storage APIs remain unchanged. An acceptance test spies on path resolution and file open/read/write calls and fails on any access to the production-resolved catalogue path; it never opens or snapshots that path. Monitor tests receive their own worker-local catalogue and prove that records cannot cross worker boundaries.

## Risks / Trade-offs

- **[Streaming redaction releases text before all future chunks are known]** → Retain algorithmically sufficient per-detector candidate state, never emit an unresolved raw structural value, and table-test every canonical detector across prefix/value splits, Unicode decoder boundaries, final flush, timeout, and interruption.
- **[Concurrent pipe draining differs across platforms]** → Keep it wholly inside `internal/proc`, use standard-library primitives with Windows/POSIX tests, and preserve inherited-stdio/native paths unchanged.
- **[A database can be dropped before filestore cleanup fails]** → Return a typed partial failure with exact retained path/reason, keep audit truthful, and make retry/doctor actionable rather than pretending atomicity across PostgreSQL and filesystem.
- **[Catalogue pagination can race concurrent writes]** → Use deterministic keyset cursors over one read transaction and document that each page is a catalogue snapshot, not a global lock.
- **[Large cross-cutting delivery]** → Land P0 and each resource slice in dependency order with focused contracts and publish every commit before starting the next slice.
- **[Local paths in maintenance output are sensitive]** → Emit them only from explicit local backup/resource detail commands, never monitor/API snapshots, and apply terminal/control and secret sanitization.

## Migration Plan

1. Correct the shell wrapper and dependency verification with negative regression tests.
2. Upgrade `internal/proc` streaming/redaction/timeout events, then adopt the shared bounded progress and Rich completion projection for the enumerated long commands.
3. Stream backup downloads and harden interrupt/retained-resource behavior.
4. Apply additive backup query fields/indexes while retaining every existing UUID/event/restore row, then add backup list/show/validate/delete.
5. Add the pending→active cluster-identity migration/creation handshake and wire the existing remote restore path into the common nullable identity-aware audit: exact non-null identity for verified `active`, null/unknown for external or no-claim legacy, and refusal for `pending` or malformed/mismatched existing claims.
6. Add local UUID restore on that same nullable identity-aware preparation/audit path, preserving the same target classification and destructive-authorization boundary.
7. Add side-effect-free PostgreSQL inventory and extend guarded drop to consume the established ownership evidence with active binding and filestore handling.
8. Add resource list/doctor over the established measurements and ownership sources.
9. Isolate init tests, update documentation/release notes, and run the full unit, integration, architecture, format, type, packaging, and disposable-resource suites.

Code rollback is safe for additive CLI and result fields. If a catalogue schema version changes, deployment must first create the same operator backup required by existing catalogue migrations; rollback restores that backup before running older code. No migration deletes backup files, audit events, restore links, databases, filestores, or volumes.

## Open Questions

None. The change fixes the concrete first/next-stage behaviors in GitHub #55; prune policy, log rotation, and cluster destruction require separately authorized OpenSpec changes after resource inventory provides evidence.
