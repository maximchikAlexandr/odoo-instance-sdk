## Delivery Contract

- Task key: `MYL-348`
- OpenSpec change: `reject-local-postgresql-dump-file`
- Delivery mode: `single_wp_no_dag`
- Estimate authority: the current planning issue properties `Estimate, hours`, `Estimate min, hours`, and `Estimate max, hours`; numeric totals are intentionally stored only there.
- Estimate basis: remaining work for one experienced developer familiar with the Python project, without AI acceleration, from base revision `d810693cf0665fb680fb670f78770e8c056c66d9` to the complete acceptance contract.
- Confidence: medium. The production path and close tests are directly inspectable, while the public CLI regression may require bounded fixture/seam adjustment to exercise real preflight without environment mutation.
- Calibration: evidence-based but not calibrated against recorded project delivery durations. Tests were not run to manufacture timing evidence.

## WP-01 — Accurate caller-owned restore format preflight

**Task coverage:** OpenSpec tasks `1.1`, `1.2`, `2.1`, `2.2`, `2.3`, `3.1`, and `3.2`, each covered here exactly once. This is the only work package.

**Deliverable:** The public `odcli db restore --file` path continues to accept valid caller-owned Odoo ZIP files, rejects a recognized PostgreSQL custom dump and unknown bytes with their specified typed diagnostics, preserves malformed-ZIP corruption semantics, and proves before-effects behavior through internal and public dry-run regressions. Local native-dump restore is not added.

**Owned responsibility scope:**

- Typed backup policy errors and sanitized error transport in `src/odoo_instance_sdk/exceptions.py` and the existing CLI output path.
- Pure bounded prefix classification in `src/odoo_instance_sdk/internal/backup_validation.py`.
- Verified input evidence and local-only preflight selection in `src/odoo_instance_sdk/internal/dbprep/source_restore.py` and directly related compatibility exports in `src/odoo_instance_sdk/internal/dbprep/`.
- Focused policy, capture, TOCTOU, catalogue-DUMP, and valid-ZIP regression coverage under `tests/unit/internal/`.
- Public CLI JSON dry-run regression coverage in `tests/unit/test_cli_database.py` and directly related fixtures or test helpers.
- Directly related snapshots, fixtures, documentation, and quality-gate configuration updates are in scope only when required by the approved contract.

**Critical shared files:** `src/odoo_instance_sdk/exceptions.py`, `src/odoo_instance_sdk/internal/backup_validation.py`, `src/odoo_instance_sdk/internal/dbprep/source_restore.py`, `tests/unit/internal/test_database_preparation.py`, `tests/unit/internal/test_backup_validation.py`, and `tests/unit/test_cli_database.py`.

**Contract surface:**

- Stable typed codes and exact sanitized messages defined by the database-restore delta spec.
- Pure content-prefix outcomes `odoo_zip`, `postgres_custom_dump`, and `unknown`, derived from bytes captured by the verified no-follow read.
- Existing valid ZIP validation/materialization, source immutability, path redaction, and catalogued `BackupFormat.DUMP` behavior remain compatible.
- Rejected local inputs do not create snapshots, spawn `pg_restore`, touch databases or catalogues, or change the project default.

**Definition of done and evidence:**

- Every OpenSpec task is checked only after its code and focused assertions are complete.
- Parameterized unit evidence distinguishes valid ZIP, `PGDMP`, malformed ZIP-family content, and unknown bytes at the policy and capture boundaries.
- Public `odcli db restore --file FILE --dry-run --format json` evidence asserts the valid sanitized plan or the exact error code/message and uses fail-fast sentinels for forbidden process/database/catalogue/default-switch effects.
- Regression evidence confirms the caller-owned source remains unchanged and its path/content is absent from plans and errors.
- Regression evidence confirms the existing catalogued native-dump branch remains reachable only for `BackupFormat.DUMP` catalogue inputs.
- Focused tests and repository lint, formatting, type-check, and full unit-test gates pass, with commands and exit codes recorded for implementation review.

**Parallel-safety rationale:** One package is required by the authoritative estimate threshold and is the safest execution unit because the error types, classifier, verified evidence shape, preflight branch, and regression matrices form one tightly coupled contract across shared files. No independent sibling write zone is created.

## Estimation Evidence and Assumptions

- The decisive defect is the unconditional local `BackupFormat.ZIP` selection in `capture_selected_backup_restore()` before `validate_zip()`.
- `_verified_file()` already provides the correct no-follow streaming trust boundary, so classification needs no new I/O architecture or dependency.
- `BackupPolicyError` and the CLI's existing `code` projection provide a local analogue for stable machine diagnostics.
- Existing valid local ZIP, source-preservation, TOCTOU, catalogue dump, and CLI delegation tests reduce new fixture work.
- The main uncertainty is how much existing command construction must be retained or replaced by sentinels to exercise the real public dry-run preflight without requiring a live initialized project.
- The estimate excludes native local-dump restore, format conversion, migrations, deployment, real-Odoo acceptance, external waiting, and human approval time.
- A change to support local dump restoration, validate dump compatibility with `pg_restore`, or alter public restore-source types invalidates this estimate and requires a planning revision.
