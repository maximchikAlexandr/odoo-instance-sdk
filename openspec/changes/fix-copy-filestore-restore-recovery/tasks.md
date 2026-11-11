## 1. Authoritative COPY filestore identity

- [ ] 1.1 Resolve the immutable COPY `data_dir` from the exact ordinary/adopted checkout path after path allocation, validate checkout containment/no-follow safety, and route that same absolute identity into generated config, auxiliary runtime binding, restore provenance, verification, and cleanup.
- [ ] 1.2 Add focused planning/execution regressions proving ordinary and adopted checkouts use their own target identity while the source config, primary project filestore, caller-owned code, and unrelated filestores remain unchanged.

## 2. Source baseline materialization and verification

- [ ] 2.1 Capture the acquired/selected ZIP through the existing validated source-neutral payload, require an absent exact target, and reuse the bounded filestore materializer to populate only `<data_dir>/filestore/<target_database>` before database import.
- [ ] 2.2 Extend attachment verification to compare normalized target references with the captured archive member set, emit `copy_source_blob_missing` for source-and-target absence, and fail closed for target-only loss, unsafe paths, symlinks, non-regular files, invalid query data, or safety-limit overflow.
- [ ] 2.3 Preserve checkout warnings in the typed result and sanitized successful lifecycle evidence without inventing files or weakening archive, borrowed-backup, source-preservation, or unrelated-resource guards.

## 3. Durable post-import recovery

- [ ] 3.1 After target existence/OID proof, re-read exact complete restore provenance and promote the COPY journal to `restored` only when database, backup, endpoint, cluster, and captured `data_dir` all match; keep every incomplete/foreign/mismatched case at `restore_pending`.
- [ ] 3.2 Extend guarded `env rm` preflight to reconcile a matching legacy/current `restore_pending` row after catalog reopen and then reuse the existing idempotent direct-drop/filestore cleanup path, retaining borrowed backups and all unrelated resources.
- [ ] 3.3 Add focused regressions for immediate compensation, catalog reopen, ordinary/adopted checkout, repeated reconcile/remove, already-absent target, borrowed backup, ownership/OID/cluster/config/data-directory mismatch, active use, and unrelated database/filestore preservation.

## 4. Product verification and documentation

- [ ] 4.1 Extend the existing unit/integration fixtures for a normal file-backed attachment, source-missing blob, target-only missing blob, and exact restore-provenance read-back; do not add a parallel test framework or catalog migration.
- [ ] 4.2 Document the warning and supported repeated `odcli env rm` recovery behavior, including that `restore_pending` without exact complete ownership evidence remains protected and no direct SQLite edit is supported.
- [ ] 4.3 Extend the existing GitHub real-Odoo test path and its safe fixtures to prove a COPY checkout reads the attachment from its checkout-local target, source/primary/unrelated resources remain unchanged, a forced post-import verification failure is recoverable without reimport, and repeated removal is idempotent.
- [ ] 4.4 Run strict OpenSpec validation, focused tests, repository lint/type/test gates within the local test-time contract, and exact-head CI for suites that exceed the local limit; report commands, exit codes, and exact revision without claiming unexecuted checks.
