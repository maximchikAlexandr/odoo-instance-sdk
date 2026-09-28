## Delivery contract

- Task key: `MYL-334`
- OpenSpec change: `stabilize-runtime-stop-identity`
- Approved base: `origin/main` at `a912a95e528f1a19dd67572f1ae0f94fc713be4c`
- Delivery mode: `single_wp_no_dag`
- Topology basis: the authoritative `Estimate, hours` planning-issue property is at or below the single-package threshold, so one Implementer/WP Verifier unit owns the complete change.
- Estimate confidence: medium. The runtime, catalogue migration, redaction, CLI, and test paths are inspectable and have close local analogues; uncertainty is concentrated in preserving all owner-neutral test fakes and strict schema-equivalence fixtures while changing the runtime upsert contract.
- Calibration: uncalibrated for one experienced human developer familiar with the Python/SQLite/Click stack, without AI acceleration. No comparable active-hour history was available; tests were not run during estimation.

## WP-MYL-334-01 — Captured runtime identity and safe stop

**Task coverage:** `1.1`, `1.2`, `1.3`, `2.1`, `2.2`, `2.3`, `3.1`, `3.2`, `3.3`, `4.1`, `4.2`.

**Deliverable:** A migrated owner-neutral runtime record captures the exact secret-free identity of the immutable foreground/detached launch step, and public stop validates that captured identity with bounded field-level diagnostics while preserving every fail-closed termination and cleanup guard.

**Owned responsibility scope:**

- Runtime identity model/codec, launch capture, persistence, read/revalidation, mismatch classification, and owner-neutral cleanup behavior.
- Catalogue schema, Alembic revision, schema fingerprint/equivalence logic, and compatibility fixtures.
- Existing stop/detached/foreground, catalogue, CLI leaf/output/security tests, architecture inventories, user documentation, and changelog directly related to this contract.
- Critical shared files include `src/odoo_instance_sdk/resources/instance/identity.py`, `src/odoo_instance_sdk/resources/instance/runtime.py`, `src/odoo_instance_sdk/resources/instance/planning.py`, `src/odoo_instance_sdk/internal/proc/redaction.py`, `src/odoo_instance_sdk/storage/catalog_schema.py`, `src/odoo_instance_sdk/storage/catalog_migrate.py`, `src/odoo_instance_sdk/storage/catalog/environment.py`, and the new catalogue migration. Directly related tests, fixtures, snapshots, docs, and generated schema bookkeeping remain in this WP.

**Contract surface:**

- Runtime catalogue gains one nullable, versioned, JSON-safe launch-identity document; new managed runtime writes always populate it, while migrated legacy rows remain null.
- The document is derived from the exact executed `PreparedStep`, uses the common redaction projection, preserves argv boundaries/sensitive positions, and contains no raw or digested secret material.
- Stop authorization uses persisted owner/PID/create-time/launch evidence plus live executable, argv projection, cwd, config, and process-group checks; it does not reconstruct authority from mutable checkout inputs or infer ownership from a port.
- Successful Rich/JSON/TOON payloads and exit semantics remain stable. Rejections expose only deterministic safe component labels and retain the runtime row without signaling.

**Definition of done and evidence:**

- Every OpenSpec task checkbox has implementation and reviewable evidence in the WP branch; no production behavior outside the specified runtime lifecycle boundary changes.
- Strict OpenSpec validation passes for `stabilize-runtime-stop-identity`.
- Focused runtime-stop, detached/foreground identity, catalogue record/migration, CLI output, security/redaction, and relevant Windows/POSIX tests pass.
- Repository architecture inventory, Ruff format/check, strict mypy, and required full repository verification gates pass without new allowlists or weakened assertions.
- Tests prove ordinary project checkout/configuration drift remains stoppable, genuine protected-binding/PID/process-group changes fail closed, snapshot TOCTOU cannot clear a replacement row, legacy evidence fails closed, and secrets/control text/full argv do not leak.
- Documentation explains the stable captured-identity behavior and legacy-row limitation without publishing internal or secret data.

**Execution safety rationale:** One package is required by the estimate threshold and is also cohesive: schema/upsert signatures, immutable-step capture, stop decoding, and shared test fakes change as one contract. Splitting them would create transient incompatible catalogue/runtime states and overlapping writes in the same identity and test modules.

## Estimation evidence and assumptions

- Evidence: `runtime` currently has no launch-identity column; `BackupCatalog._upsert_runtime()` is the single owner-neutral persistence seam; foreground and detached paths both call `_persist_runtime_identity()`; `PreparedStep` already carries exact argv and sensitive indices; `internal.proc.redaction` already provides the common safe projection; `_read_runtime_identity()` and `_validate_runtime_identity()` are the shared stop boundary; existing tests cover both owner kinds, PID/process-group safety, catalogue migration, and all CLI output modes.
- Close analogues: the existing `0002` Alembic migration/schema-equivalence tests, owner-neutral stop hardening history, detached effective-logfile matching, and common process redaction/fingerprint tests.
- Assumptions: no public output schema expansion is required; comparison remains limited to executable prefix and protected bindings rather than every optional Odoo argument; secret values are never compared through a digest; no real-Odoo environment or external service is required for the core acceptance proof.
- Main uncertainty: strict migration compatibility and the number of existing fake catalog implementations that must accept the new identity argument. A newly required public schema, cross-platform live-process defect, or requirement to compare secret values exactly would invalidate the estimate properties and require planning revision.
